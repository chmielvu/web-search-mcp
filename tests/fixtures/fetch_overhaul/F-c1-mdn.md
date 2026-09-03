# 404 Not Found

HTTP response status code 404 Not Found indicates that the origin server did not find a current representation for the target resource.
MDN documents this status for web developers writing HTTP clients and servers.
A 404 response is cacheable by default. The page explains the difference between 404 and 410 Gone.
Browsers typically display a not-found document when a site returns this code.
Developers should not confuse documentation that mentions 404 Not Found with an actual missing page.
This article covers custom error pages, logging, and search-engine implications.
Related status codes include 400 Bad Request, 401 Unauthorized, 403 Forbidden, and 500 Internal Server Error.
The specification lives in RFC 9110. Examples show fetch() handling and server configuration for Nginx and Apache.
Search engines may drop URLs that persistently return 404. Soft 404s that return 200 with not-found text are discouraged.
Accessibility guidance recommends a clear heading, a search box, and links back to the homepage.
Security notes warn against leaking whether a resource exists when authorization fails; use 404 or 403 consistently.
Performance notes mention that error documents should stay small. Internationalization examples include localized not-found pages.
The remainder of this article repeats practical advice so operators can implement robust not-found handling across CDNs, origin servers, and static site generators.
Teams often track 404 rates as a quality signal. Product managers use those rates to find broken campaigns.
Technical writers keep this MDN page updated when the HTTP living standard changes.
