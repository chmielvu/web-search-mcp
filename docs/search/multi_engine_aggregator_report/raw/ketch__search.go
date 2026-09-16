package search

import "context"

// Result represents a single search result.
type Result struct {
	Title       string `json:"title"`
	URL         string `json:"url"`
	FetchedURL  string `json:"fetched_url,omitempty"`
	Description string `json:"description,omitempty"`
	Content     string `json:"content,omitempty"`
	// Backends lists the backends that returned this result under federated
	// (--multi) search. Empty (and omitted) for single-backend results, so
	// every existing single-backend output stays byte-identical.
	Backends []string `json:"backends,omitempty"`
}

// Searcher is the interface for search backends.
type Searcher interface {
	Search(ctx context.Context, query string, limit int) ([]Result, error)
}
