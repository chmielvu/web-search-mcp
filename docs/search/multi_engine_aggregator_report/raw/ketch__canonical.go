package search

import (
	"net/url"
	"strings"
)

// trackingParams is the exact blocklist of pure click/campaign-attribution
// query parameters stripped during canonicalization. Anything not on this list
// (and not utm_*) survives, because query params routinely select the page
// (?v=…, ?id=…, ?q=…, ?page=2). Under-merging costs one duplicate row;
// over-merging silently deletes a distinct result — so the list stays narrow.
var trackingParams = map[string]bool{
	"fbclid":  true,
	"gclid":   true,
	"gbraid":  true,
	"wbraid":  true,
	"dclid":   true,
	"msclkid": true,
	"yclid":   true,
	"twclid":  true,
	"igshid":  true,
	"mc_cid":  true,
	"mc_eid":  true,
	"_hsenc":  true,
	"_hsmi":   true,
	"mkt_tok": true,
}

// isTrackingParam reports whether a query parameter name is pure click/campaign
// attribution: any utm_* param, or a name on the exact blocklist above.
func isTrackingParam(name string) bool {
	if strings.HasPrefix(name, "utm_") {
		return true
	}
	return trackingParams[name]
}

// canonicalURL reduces a raw backend URL to a stable merge key so that two
// representations of the same page collapse to one key while two genuinely
// different pages do not. The key is used only for deduplication during fusion;
// the displayed URL is always an original backend URL, so a canonicalization
// mistake can mis-merge but can never emit a URL no engine returned.
//
// Rules, applied in order (see the design doc §3):
//  1. Parse; unparseable or hostless input returns the raw string verbatim.
//  2. Lowercase scheme and host (never the path).
//  3. Fold http → https.
//  4. Strip default ports (:80, :443).
//  5. Strip a single leading "www." from the host.
//  6. Drop the fragment.
//  7. Remove tracking-only query params (utm_*, plus the exact blocklist).
//  8. Sort surviving params by name (url.Values.Encode already sorts).
//  9. Strip one trailing "/" from the path unless it is exactly "/" or empty;
//     normalize an empty path to "/".
//
// v1 deliberately does NOT decode percent-escapes further, strip index.html,
// collapse mobile hosts, or apply IDN/punycode folding.
func canonicalURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return raw
	}

	scheme := strings.ToLower(u.Scheme)
	if scheme == "http" {
		scheme = "https"
	}

	host := strings.ToLower(u.Host)
	if i := strings.LastIndex(host, ":"); i != -1 {
		if port := host[i+1:]; port == "80" || port == "443" {
			host = host[:i]
		}
	}
	host = strings.TrimPrefix(host, "www.")

	path := u.Path
	switch {
	case path == "":
		path = "/"
	case path != "/":
		path = strings.TrimSuffix(path, "/")
		if path == "" {
			path = "/"
		}
	}

	// A query string that fails to parse (mangled percent-encoding, legacy
	// semicolon separators) must not silently lose pairs: u.Query() drops
	// them, which would over-merge distinct pages. Keep the raw query
	// verbatim instead — under-merging is the safe direction for a dedup key.
	q, qerr := url.ParseQuery(u.RawQuery)
	if qerr != nil {
		key := scheme + "://" + host + path
		if u.RawQuery != "" {
			key += "?" + u.RawQuery
		}
		return key
	}
	for name := range q {
		if isTrackingParam(name) {
			q.Del(name)
		}
	}

	key := scheme + "://" + host + path
	if encoded := q.Encode(); encoded != "" {
		key += "?" + encoded
	}
	return key
}
