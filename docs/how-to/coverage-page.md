# Keep the coverage page up to date

The public page `/coverage` lists where Meteolane works, what is in the works, and which
countries and regions visitors want next. Anyone can vote, with or without an account, and
can leave an email address to be told when an area is covered. This guide is for the admin.

1. **After the routing graph and Photon cover a new area**
   ([change the coverage](change-region.md), [several countries](import-geodata.md)), open
   *Coverage areas* in the admin and add the area, or open its row, and set the status to
   **Covered**. A country needs only its ISO code (`DE`); the page names it in the reader's
   language. Saving it as covered queues one mail to every confirmed address waiting for it;
   each address is deleted once its mail went out. The *Mark as covered* action does the same
   for several rows. The mail goes out when a `default` worker runs the task.

2. **To say an area is coming**, add it with the status **Planned**. It moves from the wish
   list into "In Arbeit" and still takes votes. An optional note (German and English) shows
   under it, for example "Routing zuerst, Ortssuche folgt".

3. **To let visitors vote for a region below a country** (a canton, a province), add it with
   its ISO 3166-2 code (`IT-32`), a German and an English name, and the status **Open for
   votes**. Countries need no row to be voted for.

Votes are counted per account, or per browser cookie for visitors without one; the page
ranks the areas by them, and *Coverage votes* in the admin lists single votes. No IP address
is stored. The page's counts change once a day: the first hourly maintenance pass of each UTC
day settles the votes, and until then a new vote or a withdrawal shows only to its voter. A
vote the limits refused is kept as *not accepted* (a column in the admin) and never counts,
though its voter sees it like any other; the area list in the admin counts the accepted ones
live. Addresses that were never confirmed are deleted after 7 days by the hourly maintenance
pass.

The Caddy limits for the page's endpoints are in `deploy/auth-ratelimit.caddy`.

[Documentation index](../README.md)
