package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"sync"
	"sync/atomic"

	_ "github.com/mattn/go-sqlite3"
	"github.com/pmezard/adblock/adblock"
)

const blocklistsDir = "rules/"

type urlRecord struct {
	id  int
	raw string
}

type updateRecord struct {
	id        int
	isTracker bool
}

func main() {
	// Take sqlite db path as argument
	if len(os.Args) < 2 {
		fmt.Println("Usage: go run main.go <path_to_sqlite_db>")
		return
	}
	dbPath := os.Args[1]
	fmt.Printf("Using SQLite database at: %s\n", dbPath)

	// Check that the rules directory exists, if not create it
	if _, err := os.Stat("rules"); os.IsNotExist(err) {
		err := os.Mkdir("rules", 0755)
		if err != nil {
			fmt.Println("Error creating rules directory:", err)
			return
		}
	}

	matcher, ruleInfo := downloadAndLoadRules()
	fmt.Printf("Loaded %d rules into memory.\n", len(ruleInfo))

	// Load urls from db and check if they are trackers using the matcher
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		fmt.Println("Error opening database:", err)
		return
	}
	defer db.Close()

	// Add a column to the db to mark urls as trackers or non-trackers
	_, err = db.Exec("ALTER TABLE chrome_network_requests ADD COLUMN is_tracker BOOLEAN")
	if err != nil && err.Error() != "duplicate column name: is_tracker" {
		fmt.Println("Error adding column to database:", err)
		return
	}

	rows, err := db.Query("SELECT id, request_url FROM chrome_network_requests")
	if err != nil {
		fmt.Println("Error querying database:", err)
		return
	}

	// Collect all rows up front so we can close the cursor and parallelise freely
	var records []urlRecord
	for rows.Next() {
		var r urlRecord
		if err := rows.Scan(&r.id, &r.raw); err != nil {
			fmt.Println("Error scanning row:", err)
			continue
		}
		records = append(records, r)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		fmt.Println("Row iteration error:", err)
	}

	// --- worker pool for URL matching ---
	numWorkers := runtime.NumCPU()
	jobs := make(chan urlRecord, numWorkers*2)
	results := make(chan updateRecord, numWorkers*2)
	var trackers atomic.Int64

	// Start matcher workers (matcher.Match is read-only after build, safe to share)
	var wg sync.WaitGroup
	for i := 0; i < numWorkers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for r := range jobs {
				parsed, err := url.Parse(r.raw)
				if err != nil {
					fmt.Printf("Error parsing URL %s: %v\n", r.raw, err)
					continue
				}
				req := &adblock.Request{URL: r.raw, Domain: parsed.Hostname()}
				matched, ruleId, err := matcher.Match(req)
				if err != nil {
					fmt.Printf("Error matching URL %s: %v\n", r.raw, err)
					continue
				}
				if matched {
					info := ruleInfo[ruleId]
					fmt.Printf("Tracker: %s\n  matched rule: %q\n  from file:    %s\n", r.raw, info.raw, info.file)
					trackers.Add(1)
				}
				results <- updateRecord{id: r.id, isTracker: matched}
			}
		}()
	}

	// Close results channel once all workers finish
	go func() {
		wg.Wait()
		close(results)
	}()

	// Feed jobs to workers
	go func() {
		for _, r := range records {
			jobs <- r
		}
		close(jobs)
	}()

	// Single writer goroutine keeps SQLite writes serialised
	for u := range results {
		_, err = db.Exec("UPDATE chrome_network_requests SET is_tracker = ? WHERE id = ?", u.isTracker, u.id)
		if err != nil {
			fmt.Printf("Error updating row %d: %v\n", u.id, err)
		}
	}

	fmt.Printf("Finished processing URLs. Trackers found: %d\n", trackers.Load())
}

type ruleEntry struct {
	raw  string
	file string
}

func downloadAndLoadRules() (*adblock.RuleMatcher, map[int]ruleEntry) {
	fmt.Printf("Downloading tracker lists...\n")

	type listSpec struct {
		name string
		url  string
	}
	lists := []listSpec{
		{"AdGuard_base", "https://api.github.com/repositories/22637619/contents/BaseFilter/sections"},
		{"AdGuard_Tracking", "https://api.github.com/repositories/22637619/contents/SpywareFilter/sections"},
		{"AdGuard_Mobile", "https://api.github.com/repositories/22637619/contents/MobileFilter/sections"},
		{"EasyPrivacy", "https://api.github.com/repos/easylist/easylist/contents/easyprivacy"},
		{"EasyList_without_adult", "https://api.github.com/repos/easylist/easylist/contents/easylist"},
	}

	// Download all lists concurrently
	var wg sync.WaitGroup
	for _, l := range lists {
		wg.Add(1)
		go func(name, url string) {
			defer wg.Done()
			if err := downloadRules(name, url); err != nil {
				fmt.Printf("Error downloading %s: %v\n", name, err)
			}
		}(l.name, l.url)
	}
	wg.Wait()
	fmt.Printf("Finished downloading tracker lists.\n")

	fmt.Printf("Loading rules into memory...\n")
	matcher := adblock.NewMatcher()
	ruleInfo := make(map[int]ruleEntry)
	ruleId := 0

	files, err := os.ReadDir(blocklistsDir)
	if err != nil {
		fmt.Println("Error reading rules directory:", err)
		return nil, nil
	}

	for _, file := range files {
		if file.IsDir() {
			continue
		}
		fp, err := os.Open(blocklistsDir + file.Name())
		if err != nil {
			fmt.Printf("Error opening file %s: %v\n", file.Name(), err)
			continue
		}

		rules, err := adblock.ParseRules(fp)
		fp.Close()
		if err != nil {
			fmt.Printf("Error parsing rules from file %s: %v\n", file.Name(), err)
			continue
		}

		for _, rule := range rules {
			err = matcher.AddRule(rule, ruleId)
			if err != nil {
				// Suppress expected "rule options are not supported" noise
				continue
			}
			ruleInfo[ruleId] = ruleEntry{raw: rule.Raw, file: file.Name()}
			ruleId++
		}
	}
	return matcher, ruleInfo
}

func downloadRules(name, apiURL string) error {
	resp, err := http.Get(apiURL)
	if err != nil {
		return fmt.Errorf("failed to fetch URL: %w", err)
	}
	defer resp.Body.Close()

	var items []struct {
		Name        string `json:"name"`
		DownloadURL string `json:"download_url"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&items); err != nil {
		return fmt.Errorf("failed to parse JSON: %w", err)
	}

	// Download individual rule files concurrently
	var wg sync.WaitGroup
	var mu sync.Mutex
	var firstErr error

	for _, item := range items {
		wg.Add(1)
		go func(itemName, downloadURL string) {
			defer wg.Done()
			filename := blocklistsDir + name + "_" + itemName

			fileResp, err := http.Get(downloadURL)
			if err != nil {
				mu.Lock()
				firstErr = fmt.Errorf("failed to download %s: %w", itemName, err)
				mu.Unlock()
				return
			}
			defer fileResp.Body.Close()

			out, err := os.Create(filename)
			if err != nil {
				mu.Lock()
				firstErr = fmt.Errorf("failed to create file %s: %w", filename, err)
				mu.Unlock()
				return
			}
			defer out.Close()

			if _, err := io.Copy(out, fileResp.Body); err != nil {
				mu.Lock()
				firstErr = fmt.Errorf("failed to write file %s: %w", filename, err)
				mu.Unlock()
			}
		}(item.Name, item.DownloadURL)
	}
	wg.Wait()
	return firstErr
}
