# USDA steady contract: a lookup that answers

**Goal: `find_food` gets USDA's answer when USDA has one. Today more than half of first lookups on the live site say "USDA did not answer", with a working key.**

What exists before this work, read in the code on `cb3e6bf` and measured on 2026-11-14:

| Piece | State |
|---|---|
| The search request | One GET to `/foods/search`, with the query, the page size and three `dataType` values in the address, spaces as `%20` (`app/services/usda.py`, `_get`) |
| The food-by-id request | One GET to `/food/{id}`, nothing else in the address |
| Tries | One. Any failure ends the lookup |
| Time | A 5 second socket limit on the one try |
| Why it failed | Recorded since the trace work: one word on the call's row and one warning line |
| A comment in `_get` | Says FoodData Central answers 400 to the Survey data type "written with a +". The measurements below do not support it: the 400 comes with `%20` too |
| Measured on the live site, through `find_food`, with the owner's key | 25 words nobody had looked up before: about 15 said "USDA did not answer". The same word then answered on a second try (roti, salmon). One word sent four times at once: three answered, one did not |
| Measured from another network, with USDA's public demo key | The live request, 8 words: 3 got "400 Bad Request", an nginx page, not USDA's JSON. Unencoded brackets changed nothing (2 of those 3). The same search with no `dataType`: 20 of 20 answered, 6 of them two-word queries |
| What USDA documents | A POST form of the search, its filter in a JSON body: "The dataType parameter values need to be specified as an array." 1,000 requests an hour, and "Exceeding this limit will cause the API key to be temporarily blocked for 1 hour" |
| Not measured | The POST form against USDA, and how long a 400 takes to come back: the demo key was over its daily limit on the build machine |

So: the key is right, the request is well formed, and USDA's own front end rejects it at random when the filter rides in the address.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **The search goes as a POST with a JSON body:** `{"query": ..., "dataType": [the three], "pageSize": 25}`, `Content-Type: application/json`. The address is the path alone. The key stays in the `X-Api-Key` header | The random 400 was only ever seen with the filter in the address, and USDA documents this form. The person's query also leaves the address |
| 2 | **A failed try is tried again,** for these reasons only: `bad_request`, a status from 500 to 599, `no_answer`. Three tries at most, with no pause, and no new try once 8 seconds have gone since the first began | Measured: the same request fails and then works. Three tries is the most a lookup may cost of the hourly allowance |
| 3 | **Never tried again:** `key_refused`, `rate_limited`, `redirect`, `bad_reply`, `no_energy` | A second try cannot change them, and trying again while over the limit lengthens USDA's one-hour block |
| 4 | **The food-by-id request** keeps its GET and gets the same rule | Its address carries no filter. The rule costs nothing when it answers |
| 5 | **The row says how many tries it took.** When more than one: `usda_tries=N` in the call's notes, beside `usda=reason` when it failed in the end. One warning line for each failed try, with its number. A call that makes several lookups (`log_food` with several USDA foods) notes the most tries any one of them took | The owner can see a lookup that needed three tries, and one that failed after three |
| 5a | **`log_food` stops looking up at the first lookup that fails.** The call is refused on that food anyway, and the foods not looked up answer "USDA did not answer." as a failed one does today | One call can name 30 USDA foods. With three tries each that was up to 90 requests, and every one after a spent limit lengthens USDA's block |
| 6 | **The messages do not change.** A lookup that fails after its tries answers as it does today | Nothing the assistant reads changes |
| 7 | **The comment about "+" goes.** In its place, two lines on what was measured | The next reader should not chase it |

## Work package: one builder

| Package | Files | Exit check |
|---|---|---|
| U1 | `app/services/usda.py`, `app/services/food.py` (decision 5a only), `app/calllog.py` (reading a note back, for decision 5), `tests/test_usda.py`, `tests/test_food.py`, and `tests/test_trace.py` only where a USDA test of the trace work must follow the new request. Any other existing test only where an assertion now fails because of a decision, named in the report | The search is a POST: its method, its body as JSON with the three data types as a list, its content type, the key in the header only, and an address with no `?`. A 400 then an answer: the lookup succeeds, two requests were made, and the row says `usda_tries=2`. Three 400s: the lookup fails after exactly three, with `usda=bad_request usda_tries=3`. A 503 and a time-out are tried again; a 403, a 429, a redirect and a broken reply are not: one request each. No fourth try, and no new try after 8 seconds by a clock the test controls. The food-by-id request is still a GET and follows the same rule. The query, the key and the address are in no row, log line, error or result. The tests for the files touched pass, plain and with `-W error` |

Not in this work: asking USDA why its front end does this; a pause between tries; a different page size; the docs (`BUILD_LOG.md`), done after the review.

**Before the merge, the lead measures the POST form against USDA itself with the public demo key, beside the GET form, and records both in the build log. If the POST form is rejected at random too, decision 1 is looked at again and decision 2 stands alone.**
