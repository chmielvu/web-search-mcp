package search

import (
	"fmt"
	"io"
	rand "math/rand/v2"
	"net/http"
	"strings"
)

// keyPool is an immutable set of provider API keys. Production pools use the
// concurrent-safe package-level rand source; tests can replace randIntN with a
// deterministic function.
type keyPool struct {
	keys     []string
	randIntN func(int) int
}

func newKeyPool(keys []string) keyPool {
	clean := make([]string, 0, len(keys))
	seen := make(map[string]struct{}, len(keys))
	for _, key := range keys {
		key = strings.TrimSpace(key)
		if key == "" {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		clean = append(clean, key)
	}
	return keyPool{keys: clean, randIntN: rand.IntN}
}

func (p keyPool) pick() string {
	if len(p.keys) == 0 {
		return ""
	}
	return p.keys[p.randIntN(len(p.keys))]
}

func (p keyPool) size() int { return len(p.keys) }

// pickDifferent chooses a key other than excluded without a retry loop. Pools
// are de-duplicated at construction, so size > 1 guarantees a different key.
func (p keyPool) pickDifferent(excluded string) string {
	if len(p.keys) <= 1 {
		return p.pick()
	}
	excludedIndex := -1
	for i, key := range p.keys {
		if key == excluded {
			excludedIndex = i
			break
		}
	}
	if excludedIndex < 0 {
		return p.pick()
	}
	index := p.randIntN(len(p.keys) - 1)
	if index >= excludedIndex {
		index++
	}
	return p.keys[index]
}

// keyLabel identifies a credential without exposing it in an error message.
func (p keyPool) keyLabel(key string) string {
	for i, candidate := range p.keys {
		if candidate == key {
			return fmt.Sprintf("key %d of %d", i+1, len(p.keys))
		}
	}
	return "without an API key"
}

func closeSearchResponse(resp *http.Response) {
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	_ = resp.Body.Close()
}
