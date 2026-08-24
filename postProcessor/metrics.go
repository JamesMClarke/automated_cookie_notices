package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"unicode"
)

// kbElement is one entry from pre_keyboard_nav.json.
type kbElement struct {
	Tag       string  `json:"tag"`
	Role      *string `json:"role"`
	Text      string  `json:"text"`
	AriaLabel *string `json:"aria_label"`
	InDialog  bool    `json:"in_dialog"`
}

type kbNav struct {
	Elements []kbElement `json:"elements"`
}

func loadKeyboardNav(path string) *kbNav {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var kb kbNav
	if err := json.Unmarshal(data, &kb); err != nil {
		return nil
	}
	return &kb
}

// nvdaNavigation matches the navigation object inside pre_nvda.json.
type nvdaNavigation struct {
	FullText     string   `json:"full_text"`
	ReadingOrder []string `json:"reading_order"`
	Headings     []string `json:"headings"`
	Links        []string `json:"links"`
	Landmarks    []string `json:"landmarks"`
	FormFields   []string `json:"form_fields"`
	Images       []string `json:"images"`
}

type nvdaData struct {
	Navigation nvdaNavigation `json:"navigation"`
}

type waveStatistics struct {
	PageTitle string `json:"pagetitle"`
}

type waveReportWithStats struct {
	Statistics waveStatistics `json:"statistics"`
}

const (
	metricNA   = -1
	metricFail = 0
	metricPass = 1
)

// Keywords that identify cookie notice content in NVDA text.
// Mirrors _BASE_COOKIE_WORDS + _LANG_COOKIE_WORDS in chrome_scan.py.
var cookieNoticeKeywords = []string{
	// en
	"cookie", "consent", "privacy", "gdpr",
	"tracking", "personal data", "data protection",
	// de
	"datenschutz", "einwilligung", "cookie-richtlinie",
	// fr
	"confidentialité", "consentement", "données personnelles", "traceurs",
	// es
	"privacidad", "consentimiento", "datos personales",
	// it
	"informativa sui cookie", "consenso", "dati personali",
	// nl
	"cookiebeleid", "toestemming", "persoonsgegevens",
	// pt
	"privacidade", "consentimento", "dados pessoais",
}

// Keywords that identify interactive cookie notice elements (buttons/links).
// Mirrors _BASE_ACTION_WORDS + _LANG_ACTION_WORDS in chrome_scan.py.
var cookieActionKeywords = []string{
	// en
	"accept", "agree", "allow", "reject", "decline", "refuse",
	"settings", "preferences", "manage", "got it", "dismiss",
	"only necessary", "only essential", "i understand", "i accept",
	// de
	"akzeptieren", "zustimmen", "erlauben", "ablehnen", "einstellungen",
	"nur notwendige", "schließen", "ich verstehe",
	// fr
	"accepter", "refuser", "paramètres", "gérer", "je comprends",
	"continuer sans accepter", "uniquement nécessaires", "fermer",
	// es
	"aceptar", "rechazar", "ajustes", "gestionar", "entendido",
	"solo necesarias", "cerrar", "de acuerdo",
	// it
	"accetta", "rifiuta", "impostazioni", "gestisci", "capisco",
	"solo necessari", "chiudi",
	// nl
	"accepteren", "weigeren", "instellingen", "beheren", "begrepen",
	"alleen noodzakelijke", "sluiten", "akkoord",
	// pt
	"aceitar", "recusar", "configurações", "gerir", "entendi",
	"apenas necessários", "fechar", "concordo",
	// no
	"godta", "avvis", "innstillinger", "administrer", "bare nødvendige", "lukk",
}

// cookieConsentSubjectTerms are words that make a consent button label specific —
// they tell the user what they are accepting/rejecting, not just that an action
// is being taken.
var cookieConsentSubjectTerms = []string{
	"cookie", "cookies", "data", "privacy", "tracking", "consent",
	"essential", "necessary", "analytics", "marketing", "advertising",
	"functional", "optional", "performance",
}

// Abbreviations that should be explained if used in a cookie notice.
var abbreviationsToCheck = map[string][]string{
	"GDPR": {"general data protection regulation"},
	"CCPA": {"california consumer privacy act"},
	"PECR": {"privacy and electronic communications regulations"},
	"IAB":  {"interactive advertising bureau"},
	"TCF":  {"transparency and consent framework"},
	"CMP":  {"consent management platform"},
	"DPA":  {"data protection act", "data protection authority"},
	"ICO":  {"information commissioner"},
	"CPRA": {"california privacy rights act"},
	"LGPD": {"lei geral de proteção de dados"},
	"DSA":  {"digital services act"},
}

func evaluateScreenReaderMetrics(dbPath, artifactPath string, reclassify bool) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		fmt.Printf("[metrics] Error opening database: %v\n", err)
		return
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS screen_reader_metrics (
		id                           INTEGER PRIMARY KEY AUTOINCREMENT,
		scan_id                      INTEGER NOT NULL REFERENCES chrome_scans(id),
		domain                       TEXT    NOT NULL,
		metric_readable              INTEGER,
		metric_immediately_read      INTEGER,
		immediately_read_distance    INTEGER,
		metric_keyboard_nav          INTEGER,
		metric_link_purpose          INTEGER,
		metric_abbreviations         INTEGER,
		metric_page_titled           INTEGER,
		metric_notice_titled         INTEGER,
		metric_headings_useful       INTEGER,
		metric_no_timing             INTEGER,
		notes                        TEXT,
		created_at                   DATETIME DEFAULT CURRENT_TIMESTAMP
	)`)
	if err != nil {
		fmt.Printf("[metrics] Error creating screen_reader_metrics table: %v\n", err)
		return
	}
	// Add column to existing databases that predate this field.
	_, _ = db.Exec(`ALTER TABLE screen_reader_metrics ADD COLUMN immediately_read_distance INTEGER`)
	_, _ = db.Exec(`CREATE INDEX IF NOT EXISTS idx_srm_scan_id ON screen_reader_metrics(scan_id)`)

	if reclassify {
		if _, err := db.Exec(`DELETE FROM screen_reader_metrics`); err != nil {
			fmt.Printf("[metrics] Error clearing screen_reader_metrics: %v\n", err)
			return
		}
		fmt.Println("[metrics] Cleared existing metrics for full re-run.")
	}

	rows, err := db.Query(`
		SELECT cs.id, cs.url, cs.cookie_notice_detected,
		       cs.pre_nvda_path, cs.pre_wave_path,
		       cs.pre_keyboard_nav_path
		FROM chrome_scans cs
		LEFT JOIN screen_reader_metrics srm ON cs.id = srm.scan_id
		WHERE srm.scan_id IS NULL
		ORDER BY cs.id`)
	if err != nil {
		fmt.Printf("[metrics] Error querying chrome_scans: %v\n", err)
		return
	}

	type scanRow struct {
		id                   int
		url                  string
		cookieNoticeDetected int
		preNVDAPath          sql.NullString
		preWavePath          sql.NullString
		preKBNavPath         sql.NullString
	}
	var scans []scanRow
	for rows.Next() {
		var s scanRow
		if err := rows.Scan(&s.id, &s.url, &s.cookieNoticeDetected,
			&s.preNVDAPath, &s.preWavePath, &s.preKBNavPath); err != nil {
			fmt.Printf("[metrics] Error scanning row: %v\n", err)
			continue
		}
		scans = append(scans, s)
	}
	rows.Close()

	if len(scans) == 0 {
		fmt.Println("[metrics] Nothing to evaluate.")
		return
	}
	fmt.Printf("[metrics] Evaluating %d scan(s)…\n", len(scans))

	stmt, err := db.Prepare(`INSERT INTO screen_reader_metrics
		(scan_id, domain, metric_readable, metric_immediately_read, immediately_read_distance,
		 metric_keyboard_nav, metric_link_purpose, metric_abbreviations, metric_page_titled,
		 metric_notice_titled, metric_headings_useful, metric_no_timing, notes)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		fmt.Printf("[metrics] Error preparing insert: %v\n", err)
		return
	}
	defer stmt.Close()

	blankNVDA := 0
	for i, s := range scans {
		domain := urlToDomain(s.url)
		hasNotice := s.cookieNoticeDetected == 1

		var nvda *nvdaData
		if s.preNVDAPath.Valid && s.preNVDAPath.String != "" {
			nvda = loadNVDA(resolveArtifactPath(s.preNVDAPath.String, artifactPath))
			if nvda != nil && isNVDABlank(nvda) {
				blankNVDA++
			}
		}

		var kb *kbNav
		if s.preKBNavPath.Valid && s.preKBNavPath.String != "" {
			kb = loadKeyboardNav(resolveArtifactPath(s.preKBNavPath.String, artifactPath))
		}

		wavePath := ""
		if s.preWavePath.Valid && s.preWavePath.String != "" {
			wavePath = resolveArtifactPath(s.preWavePath.String, artifactPath)
		}

		notes := make(map[string]interface{})

		readable := evalReadable(nvda, hasNotice, notes)
		immediatelyRead := evalImmediatelyRead(nvda, hasNotice, notes)
		keyboardNav := evalKeyboardNav(kb, nvda, hasNotice, notes)
		linkPurpose := evalLinkPurpose(kb, nvda, hasNotice, notes)
		abbreviations := evalAbbreviations(nvda, hasNotice, notes)
		pageTitled := evalPageTitled(wavePath, notes)
		noticeTitled := evalNoticeTitled(nvda, hasNotice, notes)
		headingsUseful := metricNA
		noTiming := metricNA

		// Raw distance: words before first cookie keyword (NULL when not applicable).
		var immediatelyReadDist sql.NullInt64
		if hasNotice && nvda != nil {
			if d := wordsBefore(nvda.Navigation.FullText); d >= 0 {
				immediatelyReadDist = sql.NullInt64{Int64: int64(d), Valid: true}
			}
		}

		notesJSON, _ := json.Marshal(notes)

		if _, err := stmt.Exec(s.id, domain,
			readable, immediatelyRead, immediatelyReadDist,
			keyboardNav, linkPurpose, abbreviations, pageTitled,
			noticeTitled, headingsUseful, noTiming,
			string(notesJSON)); err != nil {
			fmt.Printf("[metrics] Error inserting for scan %d: %v\n", s.id, err)
		}

		if (i+1)%50 == 0 || i+1 == len(scans) {
			fmt.Printf("  [metrics] [%d/%d] evaluated\n", i+1, len(scans))
		}
	}
	fmt.Printf("[metrics] Done. %d scan(s) evaluated. %d/%d NVDA transcript(s) blank.\n",
		len(scans), blankNVDA, len(scans))
}

func loadNVDA(path string) *nvdaData {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var nvda nvdaData
	if err := json.Unmarshal(data, &nvda); err != nil {
		return nil
	}
	return &nvda
}

// isNVDABlank returns true when the transcript parsed successfully but carries
// no readable text — i.e. full_text is empty or contains only image
// placeholders (￼) and whitespace.
func isNVDABlank(nvda *nvdaData) bool {
	stripped := strings.Map(func(r rune) rune {
		if r == '￼' || unicode.IsSpace(r) {
			return -1
		}
		return r
	}, nvda.Navigation.FullText)
	return stripped == ""
}

// hasCookieKeyword reports whether text contains any cookie notice keyword.
func hasCookieKeyword(text string) bool {
	lower := strings.ToLower(text)
	for _, kw := range cookieNoticeKeywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}

// isImageOnlySegment returns true if s contains only the NVDA image placeholder (￼) and whitespace.
func isImageOnlySegment(s string) bool {
	for _, r := range s {
		if r != '￼' && !unicode.IsSpace(r) {
			return false
		}
	}
	return true
}

// wordsBefore returns the number of whitespace-delimited tokens before the first
// occurrence of any cookieNoticeKeyword in text (case-insensitive), after stripping
// leading image placeholders.
func wordsBefore(text string) int {
	// Strip leading image placeholders and whitespace.
	stripped := strings.TrimLeft(text, "￼ \t\n\r")
	lower := strings.ToLower(stripped)
	firstPos := -1
	for _, kw := range cookieNoticeKeywords {
		if idx := strings.Index(lower, kw); idx >= 0 {
			if firstPos < 0 || idx < firstPos {
				firstPos = idx
			}
		}
	}
	if firstPos < 0 {
		return -1
	}
	before := strings.TrimSpace(stripped[:firstPos])
	if before == "" {
		return 0
	}
	return len(strings.Fields(before))
}

func urlToDomain(raw string) string {
	s := strings.TrimPrefix(raw, "https://")
	s = strings.TrimPrefix(s, "http://")
	s = strings.TrimPrefix(s, "www.")
	if idx := strings.IndexByte(s, '/'); idx >= 0 {
		s = s[:idx]
	}
	return s
}

func truncateStr(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}

// containsWholeWord checks if word appears as a whole word in text (ASCII, case-sensitive).
func containsWholeWord(text, word string) bool {
	idx := 0
	for {
		pos := strings.Index(text[idx:], word)
		if pos < 0 {
			return false
		}
		abs := idx + pos
		before := abs == 0 || !isASCIIAlpha(text[abs-1])
		after := abs+len(word) >= len(text) || !isASCIIAlpha(text[abs+len(word)])
		if before && after {
			return true
		}
		idx = abs + 1
	}
}

func isASCIIAlpha(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// --- Metric evaluators ---

// (i) Readable: cookie notice content is present in NVDA full_text.
func evalReadable(nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}
	if nvda == nil {
		notes["readable"] = "no nvda data"
		return metricFail
	}
	if hasCookieKeyword(nvda.Navigation.FullText) {
		notes["readable"] = "cookie keyword found in full_text"
		return metricPass
	}
	notes["readable"] = "no cookie keyword in full_text"
	return metricFail
}

// (ii) Immediately Read: cookie notice content appears within the first 30 words
// of the meaningful (non-image) text in full_text.
func evalImmediatelyRead(nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}
	if nvda == nil {
		notes["immediately_read"] = "no nvda data"
		return metricFail
	}
	n := wordsBefore(nvda.Navigation.FullText)
	if n < 0 {
		notes["immediately_read"] = "no cookie keyword found in full_text"
		return metricFail
	}
	notes["immediately_read"] = fmt.Sprintf("%d words before first cookie keyword", n)
	if n <= 30 {
		return metricPass
	}
	return metricFail
}

// (iii) Keyboard navigable: the cookie notice is reachable and navigable via keyboard.
// Primary check: in_dialog elements in pre_keyboard_nav.json (inside role=dialog /
// aria-modal). Their presence means the notice is in the tab order and has interactive
// controls. Fallback: cookie action keywords anywhere in focusable elements (for
// notices without a proper dialog wrapper). Final fallback: NVDA data for old crawls.
func evalKeyboardNav(kb *kbNav, nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}
	if kb != nil {
		// Collect elements inside the cookie notice dialog.
		var dialogLabels []string
		interactiveCount := 0
		for _, el := range kb.Elements {
			if !el.InDialog {
				continue
			}
			label := el.Text
			if el.AriaLabel != nil && *el.AriaLabel != "" {
				label = *el.AriaLabel
			}
			dialogLabels = append(dialogLabels, fmt.Sprintf("%s:%q", el.Tag, truncateStr(label, 40)))
			tag := strings.ToUpper(el.Tag)
			role := ""
			if el.Role != nil {
				role = strings.ToLower(*el.Role)
			}
			if tag == "BUTTON" || tag == "A" || tag == "INPUT" || tag == "SELECT" || tag == "TEXTAREA" ||
				role == "button" || role == "link" || role == "switch" || role == "combobox" {
				interactiveCount++
			}
		}
		if len(dialogLabels) > 0 {
			preview := dialogLabels
			if len(preview) > 3 {
				preview = preview[:3]
			}
			notes["keyboard_nav"] = fmt.Sprintf("%d in_dialog elements (%d interactive): %v", len(dialogLabels), interactiveCount, preview)
			return metricPass
		}
		// No dialog wrapper: fall back to keyword scan of all focusable elements.
		for _, el := range kb.Elements {
			label := strings.ToLower(el.Text)
			if el.AriaLabel != nil {
				label += " " + strings.ToLower(*el.AriaLabel)
			}
			for _, kw := range cookieActionKeywords {
				if strings.Contains(label, kw) {
					notes["keyboard_nav"] = fmt.Sprintf("no dialog wrapper; action keyword %q focusable (tag=%s)", kw, el.Tag)
					return metricPass
				}
			}
		}
		notes["keyboard_nav"] = "no in_dialog elements and no cookie action keywords in focusable elements"
		return metricFail
	}
	// Fallback: infer from NVDA accessibility tree (pre-keyboard_nav crawls).
	if nvda == nil {
		notes["keyboard_nav"] = "no keyboard_nav or nvda data"
		return metricFail
	}
	combined := append(nvda.Navigation.FormFields, nvda.Navigation.Links...)
	for _, item := range combined {
		lower := strings.ToLower(item)
		for _, kw := range cookieActionKeywords {
			if strings.Contains(lower, kw) {
				notes["keyboard_nav"] = fmt.Sprintf("action keyword %q in nvda interactive elements (fallback)", kw)
				return metricPass
			}
		}
	}
	notes["keyboard_nav"] = "no cookie action keywords in nvda form_fields or links (fallback)"
	return metricFail
}

// (iv) Link/button purpose: can a screen reader user identify what they are consenting
// to from the accept/reject button label alone (without surrounding context)?
// PASS: at least one action button label includes both an action term (cookieActionKeywords)
// AND a subject term (cookieConsentSubjectTerms) — e.g. "Accept all cookies".
// FAIL: action buttons found but none name the subject — e.g. labels are "Accept", "OK".
func evalLinkPurpose(kb *kbNav, nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}

	// Collect candidate action button labels from in_dialog elements first.
	var actionLabels []string
	if kb != nil {
		for _, el := range kb.Elements {
			if !el.InDialog {
				continue
			}
			label := el.Text
			if el.AriaLabel != nil && *el.AriaLabel != "" {
				label = *el.AriaLabel
			}
			lower := strings.ToLower(label)
			for _, kw := range cookieActionKeywords {
				if strings.Contains(lower, kw) {
					actionLabels = append(actionLabels, label)
					break
				}
			}
		}
	}
	// Fallback to NVDA form_fields when no dialog elements or no kb.
	if len(actionLabels) == 0 && nvda != nil {
		for _, item := range nvda.Navigation.FormFields {
			firstLine := strings.TrimSpace(strings.SplitN(item, "\n", 2)[0])
			lower := strings.ToLower(firstLine)
			for _, kw := range cookieActionKeywords {
				if strings.Contains(lower, kw) {
					actionLabels = append(actionLabels, firstLine)
					break
				}
			}
		}
	}

	if len(actionLabels) == 0 {
		notes["link_purpose"] = "no cookie action buttons identified"
		return metricFail
	}

	notes["link_purpose_buttons"] = actionLabels
	for _, label := range actionLabels {
		lower := strings.ToLower(label)
		for _, subj := range cookieConsentSubjectTerms {
			if strings.Contains(lower, subj) {
				notes["link_purpose"] = fmt.Sprintf("descriptive label found: %q", truncateStr(label, 60))
				return metricPass
			}
		}
	}
	notes["link_purpose"] = fmt.Sprintf("action button(s) lack subject term (e.g. 'cookies'): %v", actionLabels)
	return metricFail
}

// (v) Abbreviations: any known regulatory/technical abbreviation that appears in the
// cookie notice text is either (a) spelled out in full in the body text, or (b)
// referenced via a link whose label contains the abbreviation.
func evalAbbreviations(nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}
	if nvda == nil {
		notes["abbreviations"] = "no nvda data"
		return metricFail
	}
	fullText := nvda.Navigation.FullText
	fullLower := strings.ToLower(fullText)
	var unexplained []string
	for abbr, expansions := range abbreviationsToCheck {
		if !containsWholeWord(fullText, abbr) {
			continue
		}
		explained := false
		// Check body text for the expansion.
		for _, exp := range expansions {
			if strings.Contains(fullLower, exp) {
				explained = true
				break
			}
		}
		// If not in body text, check whether any link label references the abbreviation.
		if !explained {
			for _, link := range nvda.Navigation.Links {
				if containsWholeWord(strings.ToUpper(link), abbr) {
					explained = true
					notes["abbr_link_"+abbr] = truncateStr(link, 80)
					break
				}
			}
		}
		if !explained {
			unexplained = append(unexplained, abbr)
		}
	}
	if len(unexplained) == 0 {
		notes["abbreviations"] = "no unexplained abbreviations"
		return metricPass
	}
	notes["abbreviations"] = fmt.Sprintf("unexplained: %v", unexplained)
	return metricFail
}

// (vi) Page titled: WAVE statistics.pagetitle is non-empty.
func evalPageTitled(wavePath string, notes map[string]interface{}) int {
	if wavePath == "" {
		notes["page_titled"] = "no wave path"
		return metricFail
	}
	data, err := os.ReadFile(wavePath)
	if err != nil {
		notes["page_titled"] = "could not read wave file"
		return metricFail
	}
	var report waveReportWithStats
	if err := json.Unmarshal(data, &report); err != nil {
		notes["page_titled"] = "could not parse wave file"
		return metricFail
	}
	title := strings.TrimSpace(report.Statistics.PageTitle)
	if title != "" {
		notes["page_titled"] = truncateStr(title, 80)
		return metricPass
	}
	notes["page_titled"] = "empty page title"
	return metricFail
}

// (vii) Cookie notice titled: at least one NVDA heading contains cookie notice keywords.
func evalNoticeTitled(nvda *nvdaData, hasNotice bool, notes map[string]interface{}) int {
	if !hasNotice {
		return metricNA
	}
	if nvda == nil {
		notes["notice_titled"] = "no nvda data"
		return metricFail
	}
	if len(nvda.Navigation.Headings) == 0 {
		notes["notice_titled"] = "no headings found"
		return metricFail
	}
	for _, h := range nvda.Navigation.Headings {
		if hasCookieKeyword(h) {
			notes["notice_titled"] = truncateStr(strings.SplitN(h, "\n", 2)[0], 80)
			return metricPass
		}
	}
	notes["notice_titled"] = "headings present but none relate to cookie notice"
	return metricFail
}
