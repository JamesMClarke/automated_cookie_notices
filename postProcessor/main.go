package main

import (
	"bytes"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/pmezard/adblock/adblock"
)

const (
	blocklistsDir = "rules/"
	ocdURL        = "https://raw.githubusercontent.com/jkwakman/Open-Cookie-Database/master/open-cookie-database.csv"
	ocdCacheFile  = "open-cookie-database.csv"

	// Cookiedatabase.org REST API (no auth required for public endpoint)
	cdbCookiesURL = "https://cookiedatabase.org/wp-json/cookiedatabase/v1/cookies/"
	cdbBatchSize  = 50
	cdbDelay      = 200 * time.Millisecond

	// CookieSearch (cookiesearch.org) by CookieYes claims 100k+ cookies but
	// provides no REST API or downloadable dataset — web UI only, not usable here.
)

type urlRecord struct {
	id  int
	raw string
}

type updateRecord struct {
	id        int
	isTracker bool
}

func main() {
	if len(os.Args) < 3 {
		fmt.Println("Usage: go run main.go <path_to_sqlite_db> <path_to_artifacts_dir>")
		fmt.Println("Example: go run main.go data/requests.db data/artifacts/")
		return
	}
	dbPath := os.Args[1]
	artifactPath := os.Args[2]
	fmt.Printf("Using SQLite database at: %s\n", dbPath)
	fmt.Printf("Using artifacts directory at: %s\n", artifactPath)

	// matcher, ruleInfo := downloadAndLoadRules()
	// fmt.Printf("Loaded %d rules into memory.\n", len(ruleInfo))
	// identifyTrackers(matcher, ruleInfo, nil, dbPath)

	// Optional: --cdb-key <license> enables Cookiedatabase.org API enrichment.
	// var cdbKey string
	// for i, arg := range os.Args {
	// 	if arg == "--cdb-key" && i+1 < len(os.Args) {
	// 		cdbKey = os.Args[i+1]
	// 	}
	// }

	lookup, err := loadOCD(ocdCacheFile)
	if err != nil {
		fmt.Printf("Error loading Open Cookie Database: %v\n", err)
		return
	}
	classifyCookies(dbPath, artifactPath, lookup)
}

type ruleEntry struct {
	raw  string
	file string
}

func downloadAndLoadRules() (*adblock.RuleMatcher, map[int]ruleEntry) {
	// Check that the rules directory exists, if not create it
	if _, err := os.Stat("rules"); os.IsNotExist(err) {
		err := os.Mkdir("rules", 0755)
		if err != nil {
			fmt.Println("Error creating rules directory:", err)
			return nil, nil
		}
	}

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

func identifyTrackers(matcher *adblock.RuleMatcher, ruleInfo map[int]ruleEntry, urls []urlRecord, dbPath string) {
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

// ---------------------------------------------------------------------------
// Open Cookie Database – cookie classification
// ---------------------------------------------------------------------------

type ocdEntry struct {
	platform string
	category string
	pattern  string // lower-cased Cookie/Data Key
	wildcard bool
}

type cookieLookup struct {
	exact     map[string]*ocdEntry // pattern -> entry (non-wildcard)
	wildcards []*ocdEntry          // wildcard entries, longest-first
}

func loadOCD(cachePath string) (*cookieLookup, error) {
	var data []byte

	if _, err := os.Stat(cachePath); err == nil {
		fmt.Printf("[ocd] Using cached CSV at %s\n", cachePath)
		data, err = os.ReadFile(cachePath)
		if err != nil {
			return nil, fmt.Errorf("reading cache: %w", err)
		}
	} else {
		fmt.Printf("[ocd] Downloading Open Cookie Database from %s …\n", ocdURL)
		resp, err := http.Get(ocdURL)
		if err != nil {
			return nil, fmt.Errorf("downloading OCD: %w", err)
		}
		defer resp.Body.Close()
		data, err = io.ReadAll(resp.Body)
		if err != nil {
			return nil, fmt.Errorf("reading OCD response: %w", err)
		}
		if err := os.WriteFile(cachePath, data, 0644); err != nil {
			fmt.Printf("[ocd] Warning: could not cache CSV: %v\n", err)
		} else {
			fmt.Printf("[ocd] Cached to %s\n", cachePath)
		}
	}

	r := csv.NewReader(strings.NewReader(string(data)))
	headers, err := r.Read()
	if err != nil {
		return nil, fmt.Errorf("reading CSV header: %w", err)
	}

	// Map column names to indices.
	col := make(map[string]int, len(headers))
	for i, h := range headers {
		col[strings.TrimSpace(h)] = i
	}
	required := []string{"Platform", "Category", "Cookie / Data Key name", "Wildcard match"}
	for _, c := range required {
		if _, ok := col[c]; !ok {
			return nil, fmt.Errorf("CSV missing column %q", c)
		}
	}

	lk := &cookieLookup{exact: make(map[string]*ocdEntry)}

	for {
		row, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			continue
		}
		name := strings.TrimSpace(row[col["Cookie / Data Key name"]])
		if name == "" {
			continue
		}
		e := &ocdEntry{
			platform: strings.TrimSpace(row[col["Platform"]]),
			category: strings.TrimSpace(row[col["Category"]]),
			pattern:  strings.ToLower(name),
			wildcard: strings.TrimSpace(row[col["Wildcard match"]]) == "1",
		}
		if e.wildcard {
			lk.wildcards = append(lk.wildcards, e)
		} else {
			if _, exists := lk.exact[e.pattern]; !exists {
				lk.exact[e.pattern] = e
			}
		}
	}

	// Sort wildcards longest-first so more specific patterns win.
	for i := 0; i < len(lk.wildcards)-1; i++ {
		for j := i + 1; j < len(lk.wildcards); j++ {
			if len(lk.wildcards[j].pattern) > len(lk.wildcards[i].pattern) {
				lk.wildcards[i], lk.wildcards[j] = lk.wildcards[j], lk.wildcards[i]
			}
		}
	}

	fmt.Printf("[ocd] Loaded %d exact + %d wildcard patterns.\n", len(lk.exact), len(lk.wildcards))
	return lk, nil
}

func (lk *cookieLookup) classify(name string) *ocdEntry {
	key := strings.ToLower(name)
	if e, ok := lk.exact[key]; ok {
		return e
	}
	for _, e := range lk.wildcards {
		if matched, _ := path.Match(e.pattern, key); matched {
			return e
		}
	}
	return nil
}

type cookieJSON struct {
	Name   string `json:"name"`
	Domain string `json:"domain"`
}

type classificationRecord struct {
	scanID         int
	phase          string
	cookieName     string
	cookieDomain   string
	category       string
	platform       string
	matchedPattern string
	isWildcard     bool
}

// resolveArtifactPath converts a stored path (which may be a Windows absolute
// path from a different machine) to a local path by finding the "artifacts"
// directory component and re-rooting everything after it under artifactPath.
// Falls back to the raw stored value if no artifacts root is found.
func resolveArtifactPath(stored, artifactPath string) string {
	// Normalise separators so we can split on "/" consistently.
	norm := strings.ReplaceAll(stored, "\\", "/")
	parts := strings.Split(norm, "/")
	for i, p := range parts {
		if strings.EqualFold(p, "artifacts") {
			rel := filepath.Join(parts[i+1:]...)
			return filepath.Join(artifactPath, rel)
		}
	}
	// No artifacts component found — use as-is.
	if filepath.IsAbs(stored) {
		return stored
	}
	return filepath.Join(filepath.Dir(artifactPath), stored)
}

func classifyCookies(dbPath, artifactPath string, lk *cookieLookup) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		fmt.Printf("Error opening database: %v\n", err)
		return
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS cookie_classifications (
		id                   INTEGER PRIMARY KEY AUTOINCREMENT,
		scan_id              INTEGER NOT NULL REFERENCES chrome_scans(id),
		phase                TEXT    NOT NULL CHECK(phase IN ('pre','post_accept','post_reject')),
		cookie_name          TEXT    NOT NULL,
		cookie_domain        TEXT,
		category             TEXT,
		platform             TEXT,
		matched_pattern      TEXT,
		is_wildcard          INTEGER NOT NULL DEFAULT 0,
		source               TEXT,
		purpose              TEXT,
		cookie_function      TEXT,
		is_personal_data     INTEGER,
		collected_personal_data TEXT,
		retention            TEXT
	)`)
	if err != nil {
		fmt.Printf("Error creating cookie_classifications table: %v\n", err)
		return
	}
	// Migrate: add columns added after initial release.
	for _, col := range []string{
		"source TEXT",
		"purpose TEXT",
		"cookie_function TEXT",
		"is_personal_data INTEGER",
		"collected_personal_data TEXT",
		"retention TEXT",
	} {
		db.Exec(`ALTER TABLE cookie_classifications ADD COLUMN ` + col)
	}
	_, err = db.Exec(`CREATE INDEX IF NOT EXISTS idx_cc_scan_id ON cookie_classifications(scan_id)`)
	if err != nil {
		fmt.Printf("Error creating index: %v\n", err)
		return
	}

	// Find scans not yet classified.
	rows, err := db.Query(`
		SELECT id, pre_cookies_path, post_accept_cookies_path, post_reject_cookies_path
		FROM chrome_scans
		WHERE id NOT IN (SELECT DISTINCT scan_id FROM cookie_classifications)
		ORDER BY id`)
	if err != nil {
		fmt.Printf("Error querying chrome_scans: %v\n", err)
		return
	}

	type scanRow struct {
		id             int
		prePath        sql.NullString
		postAcceptPath sql.NullString
		postRejectPath sql.NullString
	}
	var scans []scanRow
	for rows.Next() {
		var s scanRow
		if err := rows.Scan(&s.id, &s.prePath, &s.postAcceptPath, &s.postRejectPath); err != nil {
			fmt.Printf("Error scanning row: %v\n", err)
			continue
		}
		scans = append(scans, s)
	}
	rows.Close()

	if len(scans) == 0 {
		fmt.Println("[ocd] Nothing to classify — all scans already processed.")
		return
	}
	fmt.Printf("[ocd] Classifying cookies for %d scan(s)…\n", len(scans))

	phases := []struct {
		name string
		fn   func(scanRow) sql.NullString
	}{
		{"pre", func(s scanRow) sql.NullString { return s.prePath }},
		{"post_accept", func(s scanRow) sql.NullString { return s.postAcceptPath }},
		{"post_reject", func(s scanRow) sql.NullString { return s.postRejectPath }},
	}

	stmt, err := db.Prepare(`INSERT INTO cookie_classifications
		(scan_id, phase, cookie_name, cookie_domain, category, platform, matched_pattern, is_wildcard, source)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		fmt.Printf("Error preparing insert: %v\n", err)
		return
	}
	defer stmt.Close()

	total := 0
	for i, s := range scans {
		for _, ph := range phases {
			p := ph.fn(s)
			if !p.Valid || p.String == "" {
				continue
			}
			cookiePath := resolveArtifactPath(p.String, artifactPath)
			data, err := os.ReadFile(cookiePath)
			if err != nil {
				continue
			}
			var cookies []cookieJSON
			if err := json.Unmarshal(data, &cookies); err != nil {
				fmt.Printf("[ocd] Warning: could not parse %s: %v\n", cookiePath, err)
				continue
			}
			for _, c := range cookies {
				match := lk.classify(c.Name)
				var category, platform, matchedPattern, source string
				isWildcard := 0
				if match != nil {
					category = match.category
					platform = match.platform
					matchedPattern = match.pattern
					source = "ocd"
					if match.wildcard {
						isWildcard = 1
					}
				}
				if _, err := stmt.Exec(s.id, ph.name, c.Name, c.Domain,
					nullStr(category), nullStr(platform), nullStr(matchedPattern), isWildcard, nullStr(source)); err != nil {
					fmt.Printf("[ocd] Error inserting record: %v\n", err)
					continue
				}
				total++
			}
		}
		if (i+1)%10 == 0 || i+1 == len(scans) {
			fmt.Printf("  [ocd] [%d/%d] %d cookie records written\n", i+1, len(scans), total)
		}
	}

	fmt.Printf("[ocd] Done. %d cookie classification records written.\n", total)

	enrichFromCDB(db)
}

// enrichFromCDB fetches classifications from Cookiedatabase.org for any cookie
// in cookie_classifications that has no category yet, and updates those rows.
func enrichFromCDB(db *sql.DB) {
	// Collect distinct unclassified cookie names.
	rows, err := db.Query(
		`SELECT DISTINCT cookie_name FROM cookie_classifications WHERE category IS NULL`)
	if err != nil {
		fmt.Printf("[cdb] Error querying unknowns: %v\n", err)
		return
	}
	var unknowns []string
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err == nil && name != "" {
			unknowns = append(unknowns, name)
		}
	}
	rows.Close()

	if len(unknowns) == 0 {
		fmt.Println("[cdb] No unclassified cookies — skipping Cookiedatabase.org lookup.")
		return
	}
	fmt.Printf("[cdb] Querying Cookiedatabase.org for %d unrecognised cookie name(s)…\n", len(unknowns))

	// Batch into groups and query the API.
	found := make(map[string]*cdbResult)
	for i := 0; i < len(unknowns); i += cdbBatchSize {
		end := i + cdbBatchSize
		if end > len(unknowns) {
			end = len(unknowns)
		}
		batch := unknowns[i:end]
		results, err := queryCDB(batch)
		if err != nil {
			fmt.Printf("[cdb] Warning: batch %d–%d failed: %v\n", i+1, end, err)
		} else {
			for k, v := range results {
				found[k] = v
			}
		}
		if end < len(unknowns) {
			time.Sleep(cdbDelay)
		}
	}

	if len(found) == 0 {
		fmt.Println("[cdb] Cookiedatabase.org returned no matches.")
		return
	}

	// Update cookie_classifications rows that now have a match.
	updStmt, err := db.Prepare(
		`UPDATE cookie_classifications
		 SET category = ?, platform = ?, purpose = ?, cookie_function = ?,
		     is_personal_data = ?, collected_personal_data = ?, retention = ?,
		     source = 'cookiedatabase.org'
		 WHERE cookie_name = ? AND category IS NULL`)
	if err != nil {
		fmt.Printf("[cdb] Error preparing update: %v\n", err)
		return
	}
	defer updStmt.Close()

	updated := 0
	for name, r := range found {
		res, err := updStmt.Exec(
			nullStr(r.category), nullStr(r.platform), nullStr(r.purpose),
			nullStr(r.cookieFunction), r.isPersonalData, nullStr(r.collectedPersonalData),
			nullStr(r.retention), name,
		)
		if err != nil {
			fmt.Printf("[cdb] Error updating %q: %v\n", name, err)
			continue
		}
		n, _ := res.RowsAffected()
		updated += int(n)
	}
	fmt.Printf("[cdb] Updated %d row(s) from Cookiedatabase.org (%d name(s) matched).\n",
		updated, len(found))
}

type cdbResult struct {
	category              string
	platform              string
	purpose               string
	cookieFunction        string
	isPersonalData        interface{} // 0/1/null
	collectedPersonalData string
	retention             string
}

// queryCDB posts a batch of cookie names to the Cookiedatabase.org API and
// returns a map of lowercased cookie name -> result for those that matched.
//
// Request format (from working Python implementation):
//
//	{"cdb_license": "...", "en": {"cookies": ["_ga", "_gid", ...]}}
//
// Response: {"data": {"en": {"_ga": {...fields...}}}, "status": 200}
func queryCDB(names []string) (map[string]*cdbResult, error) {
	payload := map[string]interface{}{
		"en": map[string]interface{}{
			"cookies": names,
		},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Post(cdbCookiesURL, "application/json", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("HTTP error: %w", err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading response: %w", err)
	}

	// Top-level envelope: {"data": ..., "status": 200}
	var envelope struct {
		Data   json.RawMessage `json:"data"`
		Status int             `json:"status"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, fmt.Errorf("parsing envelope: %w", err)
	}
	if envelope.Status != 200 || envelope.Data == nil {
		return nil, nil
	}

	// data is [] when no matches, or {"en": {"cookie_name": {...}}} on hits.
	var outer map[string]map[string]json.RawMessage
	if err := json.Unmarshal(envelope.Data, &outer); err != nil {
		return nil, nil // empty array — no matches
	}

	results := make(map[string]*cdbResult)
	for cookieName, rawCookie := range outer["en"] {
		var entry map[string]json.RawMessage
		if err := json.Unmarshal(rawCookie, &entry); err != nil {
			continue
		}
		r := &cdbResult{}
		jsonStr := func(key string) string {
			if v, ok := entry[key]; ok {
				var s string
				json.Unmarshal(v, &s)
				return s
			}
			return ""
		}
		r.platform = jsonStr("service")
		r.purpose = jsonStr("purpose")
		r.cookieFunction = jsonStr("cookieFunction")
		r.collectedPersonalData = jsonStr("collectedPersonalData")
		r.retention = jsonStr("retention")
		// "type" maps to the OCD "Category" equivalent in this API
		r.category = jsonStr("type")
		if r.category == "" {
			r.category = jsonStr("purpose")
		}
		// isPersonalData is a bool/int in the response
		if v, ok := entry["isPersonalData"]; ok {
			var b bool
			if json.Unmarshal(v, &b) == nil {
				if b {
					r.isPersonalData = 1
				} else {
					r.isPersonalData = 0
				}
			}
		}
		results[strings.ToLower(cookieName)] = r
	}
	return results, nil
}

func nullStr(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}
