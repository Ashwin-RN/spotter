# Data on request contract: any question about a person's own data gets Spotter's numbers

**Goal: when a question is one the other reads do not answer ("how do I get better at basketball?", "where do I cut calories day to day?"), the assistant calls `what_is_logged` to see what is there, then `get_data` for the rows and totals it needs, and quotes Spotter's figures. Spotter works out every number. Nothing is written.**

Its card was approved by the owner on 2026-11-16, every pick, decisions 1 to 21, with two later words of his the same day: the function list kept small, and decision 15 changed (below). This contract waits for his yes before the build. Not in this build: the card's "Scoped, not in this build" table, its open item 2 (Basketball as an activity) and open item 1 (streaks, a later item). Built on `52b5dd8`.

Reconciled 2026-11-16 after the owner's later changes: null taken as left out, `targets` beside the data, weighted muscles, and a review's fixes.

## His words

On new kinds of training: "they have very prefix structure as far as what are the events and what are the times ... it should scale to these guys easily without too much complication. It should be simple enough that we're able to add these features relatively simply." Later he narrowed it to workouts (yoga, Pilates, Hyrox, and BJJ and boxing if they fit), not team sports, and not in this build: so the scaling rule below.

On the function list: "we can have a small menu ... If they want more details, then they can call another function directly, right? Rather than having them read a big function list every time they try accessing this." So both descriptions stay short, every field's meaning is in `what_is_logged`'s answer, and each entry has a size bar.

On words: "a target should be like something you hit once, whereas goal is something that is ... more of an ongoing thing." So in his words the daily and weekly amounts (kcal, steps, sessions a week) are goals, and finish lines by a date are targets. The code says the reverse: `set_goal` makes finish lines, and `update_settings` holds the daily and weekly targets. Which words the published functions end up with is settled later. So this contract adds no published name that may have to flip: both answers carry the daily and weekly amounts only as `whoami` publishes them, `targets` with its `Targets` model, and the open finish-line goals ride under the word `get_goals` publishes, `goals`, as a subset of `GoalNumbers`.

## What exists today

Read on `52b5dd8` on 2026-11-16. The builder reads these rows again on the commit it starts from.

| Piece | State | Where |
|---|---|---|
| The person's tables | `lift_session` and `lift_set` (a set has no `user_id`, it is owned through its session), `bodyweight`, `measurement`, `food_entry`, `cardio_entry`, `saved_food`. Every stored weight is kg and every distance km. Dates are the person's own date | `app/models.py:152-187`, `:190-203`, `:320-336`, `:339-352`, `:375-424`, `:9` |
| Soft delete | `deleted_at` on the five entry tables and on `saved_food`; every read filters it itself | `app/models.py:92-98`, `:352`, `app/services/trends.py:194-199` |
| Tables with a user id | `models.user_tables()` lists the tables with a `user_id` column, so it misses `lift_set` | `app/models.py:451-454`, `:171` |
| Exercise list | 41 lifts with a movement category and 15 cardio activities, code synced into `exercise` by name. `sync` builds `Exercise(**row)` from `rows()`, so a new key in `rows()` needs a column. No body region anywhere | `app/exercises.py:17-62`, `:64-87`, `:91-116` |
| Derived numbers | Epley, null outside 1 to 10 reps or at 0 kg, 2 dp; pace as m:ss; speed; cardio kcal. Volume is worked out nowhere | `app/metrics.py:122-132`, `:194-207`, `:188-191` |
| An entry after today | Counts as today's in every window (`metrics.on_or_before`) | `app/metrics.py:90-113` |
| Rounding for a person | `formatting.show` gives text; `formatting.rounded` gives a number but takes no units, so a lb number field does not exist today. No `minutes` or `volume` quantity | `app/formatting.py:236-254`, `:290-312`, `:62-99` |
| Units | `users.units_for(user)`; lb and mi rows in `formatting._SHOWN_IN`; number fields stay kg and km by name (units decision 8) | `app/users.py:265-267`, `app/formatting.py:102-119`, `docs/contracts/units.md:66-67` |
| Goals | `goals.load_numbers` then `goals.goal_numbers`, `rounded_numbers`, `label`, `plan_line`; `get_goals`' summary line "{label} by {day}, {status}." | `app/services/goals.py:286-312`, `:386`, `:427-443`, `:472-525`, `:928-939` |
| The settings' daily and weekly amounts | `user.calorie_target_kcal`, `target_sessions_per_week`, `target_cardio_minutes_per_week`, published by `whoami` as `targets` (`Targets`), and `whoami` is called first | `app/models.py:123-125`, `app/mcp_schemas.py:367-370`, `:412` |
| A goal's published shape | `GoalNumbers`, under `goals` in `get_goals` | `app/mcp_schemas.py:283-362`, `:1055-1057` |
| An empty account | `account_empty` counts live entries, live saved foods and open goals, never settings | `app/services/account.py:165-175` |
| A refusal | `rules.error`, `rules.combine`; `match_exercise` and `unknown_exercise` with closest names; `match_kind`; `match_meal`; `to_date` (YYYY-MM-DD only) | `app/rules.py:289-302`, `:314-328`, `:335-357`, `:384-395`, `:464-471` |
| `get_log`'s floor | `log.oldest_day(user)`: created_at's UTC date less `OLDEST_DAYS` less one day; `date_too_old` with "the oldest day this account can hold" | `app/services/log.py:44-47`, `:100-104` |
| The read limit | Every function at `read` in `NEEDS` counts toward 300 in 10 minutes, checked before the work | `app/mcp_server.py:241-257`, `:657-658`, `app/services/common.py:413-424` |
| Annotations | `READ_ONLY`: title, `readOnlyHint` true, `destructiveHint` false, `idempotentHint` true, `openWorldHint` false | `app/mcp_server.py:229` |
| Order of the list | Non-owner functions in definition order, owner functions last, `get_routines` the last non-owner one | `app/mcp_server.py:1384-1400`, `:586-589` |
| An argument named `from` | Not possible: the library builds the argument model from the function's own parameters, and `from` is not a valid parameter name (`inspect.Parameter` raises "'from' is not a valid parameter name", run on 2026-11-16) | `.venv/Lib/site-packages/mcp/server/mcpserver/utilities/func_metadata.py:318-350` |
| Unknown keys | Every function publishes `additionalProperties: false` at its top level; `SpotterServer.call_tool` refuses an unknown key before the library | `app/mcp_server.py:1499-1500`, `:593-607` |
| The trace | `KNOWN_NAMES`, and a test that holds them equal to every published property name. An object argument is written as its known keys only, never values | `app/calllog.py:63-75`, `:180-205`, `tests/test_trace.py:225-233` |
| The instructions | 3,927 characters (printed on this tree). Two tests pin that they end with the plan_line sentence. Seven files pin 3,927 | `app/mcp_server.py:197-222`, `tests/test_mcp.py:2071`, `:2519`, see "Pins that move" |
| Result models | Top-level models forbid extras; every field described; `every_result` must produce every top-level field; `unmodelled` walks nested models only, so a `list[list]` or a plain `dict` field is not walked | `app/mcp_schemas.py:35`, `:1463-1489`, `tests/test_mcp.py:317`, `:535-563` |
| The size of the list | The 22 functions everyone sees publish 161,663 characters, 109,059 of it output schemas; `get_progress` alone is 17,848 (17,173 output schema). Measured by the coordinator through `build_server`; the output schema figure was matched here on a bare server, 17,173 | coordinator, 2026-11-16 |
| Evals | 43 cases. `NO_DATA_SERVICES = {"four_weeks"}`, and `seed_service` returns `four_weeks` for any name in that set. `matches` has no "key present" form | `evals/cases.json`, `evals/judge.py:80`, `:212-222`, `:237-251` |

## Decisions

The card's 21, as approved. Where the code made a pick concrete, the ruling below says how.

| # | Decision |
|---|---|
| 1 | Structured, not SQL: listed areas, fields, filters, group by, totals |
| 2 | The catalogue is a function's answer, `what_is_logged` |
| 3 | One `get_data` plus `what_is_logged`. Today's reads stay unchanged |
| 4 | One field list in code, tied to `app/models.py` by a test |
| 5 | Areas: `lift_sets`, `lift_sessions`, `cardio`, `weigh_ins`, `measurements`, `food`, `saved_foods`, `days` |
| 6 | Derived on a row: estimated max (Epley), volume = weight × reps (0 kg counts 0); pace, speed and cardio kcal as stored or worked out today |
| 7 | Filters: exercise, category, region, muscle, activity, measurement kind, meal, source, estimated. Names as `list_exercises` gives them; a wrong name refused with the closest |
| 8 | A range, left out the last 28 days ending today, always in the answer, with a note when it was the default. Weeks Monday to Sunday, calendar months, a part period says its days in range |
| 9 | Group by day, week, month, or a field (exercise, category, region, muscle, activity, kind, meal, food), up to two |
| 10 | Totals where they mean something: sum, avg, max, min, count, per_day, share |
| 11 | Order by any column, newest first by default. 100 rows, at most 300, the answer under 30,000 characters. `more` is an opaque cursor with no server state |
| 12 | Rows as lists under a columns header. Nothing in range: empty rows and a note with the last entry's date. A field never logged is null |
| 13 | Values in the person's units at units decision 9's steps, the unit in the column name. **A departure from units decision 8** ("Number fields stay in kg and km", `docs/contracts/units.md:66`): here a column's name carries the person's unit (`weight_lb`), so the assistant reads lb as lb and never converts, which decision 8 guarded against by keeping kg in the name. `get_data`'s description says so. `whoami`'s units text (`app/mcp_schemas.py:413-419`) is not touched |
| 14 | Deleted entries always left out |
| 15 | `what_is_logged`: per area and per field, the names logged, every open goal with its plan line, and the daily and weekly amounts as `whoami` gives them (R5). **Changed twice by the owner on 2026-11-16:** the targets were first dropped for the words; then put back, since a calorie amount belongs with the food data ("Somebody is trying to lose weight and they're trying to eat 2,100 calories a day. That would be relevant"), under `whoami`'s own name and shape |
| 16 | `get_data` carries the open goals that match its filters, and the daily and weekly amounts (R5) |
| 17 | Body region and the muscles each of the 41 lifts works, from free-exercise-db, in `app/exercises.py`, no migration: each primary muscle counts 1 a set, each secondary 0.5 (R13). **The muscle added by the owner on 2026-11-16:** "what about splits across push and pull muscles or individual muscle groups? ... we should be able to identify it right". Push against pull is a group by category. **Then, the same evening:** "Not everybody splits it as a push-pull legs ... close grip bench press would be triceps heavy, but there is some chest in it. How would you account for that accurately? ... It's about tagging the exercise and the involvement accurately, right? Or at least semi-accurately." So every muscle a lift works is tagged, weighted |
| 18 | One sentence in the instructions; `docs/can-and-cant.md` gains both; read-only annotations |
| 19 | No migration; a year timed on SQLite; one local Postgres run of the new test file before the merge (exit check 6) |
| 20 | 8 eval cases on a new 16-week seed |
| 21 | The new input names join the call log's known names |

## Rulings on the how

| # | Ruling | Why |
|---|---|---|
| R1 | **The range inputs are `from_date` and `to_date`.** The answer keeps `from` and `to`, as `get_log` does (`app/mcp_schemas.py:1028-1032`) | `from` cannot be a parameter (see "What exists") |
| R2 | **Every optional input of `get_data` takes null as left out,** published as `X | None` with a None default, as today's optional inputs are, such as `get_log`'s `date` and `days` (`app/mcp_server.py:998-1002`). `group_by`'s 1 to 2 holds on the nullable list (R11): a pydantic check on 2026-11-16 took null and refused 0 and 3 items | Some clients send null for every optional field they leave empty. Refusing it would make `get_data` the one function that does, and "any AI" must be able to call it. The nullable form measured 387 characters more than a plain type; with it, `get_data`'s entry measured 6,994, so the size bar is 7,500 (see "The size of the list") |
| R3 | **A field takes either unit's name.** On any account, `weight_kg` and `weight_lb` name the same field, in `fields`, `totals` and `order`; the answer's columns are always in the person's units. No refusal for the other unit's name | The assistant never converts, and never needs to know which name the account uses before it asks |
| R4 | **The open goals ride under `goals`, in both functions, each a subset of `GoalNumbers`:** `goal_id`, `label`, `status`, `target_date`, `plan_line`, each the `GoalNumbers` field of that name, worked as `get_goals` works it. The model is `OpenGoal`; its descriptions are shorter than `GoalNumbers`' and point to `get_goals` | The owner's word on names: the word `get_goals` already publishes. A subset keeps the output schema under the size bar: `GoalNumbers` whole is most of `get_progress`' 17,173 |
| R5 | **Both answers carry `targets`: the `Targets` model `whoami` publishes (`app/mcp_schemas.py:367-370`), the same name, shape and values,** worked as `whoami` works them. Null fields where not set. Nothing else from the settings. **In `get_data`, on every area, always present** (not optional): one shape for every answer, the three amounts bear on food, lifts, cardio and days, and on the others they cost about 110 characters an answer. **The values:** `app/services/data.py` reads the three columns off the user row it already holds (`user.calorie_target_kcal`, `user.target_sessions_per_week`, `user.target_cardio_minutes_per_week`), the dict `account.whoami` builds (`app/services/account.py:111-115`). `app/services/account.py` is not touched; a test holds both answers' `targets` equal to `whoami`'s | The owner's word: the calorie amount must sit beside the food data. Reusing `whoami`'s name and model adds no published name: a later rename of the words moves all three answers together |
| R6 | **The cursor is an offset**, not a key: an entry logged or deleted between two pages can move a row across the page edge (known limit) | Any column can be the order, so a key cursor would need every column |
| R7 | **The 30,000 ceiling cuts the page, whatever `limit` says.** Rows are added while the answer's compact JSON, counted with its final summary, `more` and note, stays at or under 30,000 characters; the cursor's place counts the rows given, so it never depends on `limit` | 300 food rows with 200-character descriptions measured 142,844 characters |
| R8 | **Shares are worked over every group of the range, before order, limit and paging.** A page need not sum to 100 | One figure per group, the same on every page |
| R9 | **Every rule an area or a field has is data on its entry** (the hooks in the dataclasses below): a row's own quantity, the quantity of each total, a guard on a total, a ratio avg, the divisor of per_day, how a filter value is matched and refused, whether a field is a default column, a group's key and shown cell when they differ from the value, a fan, which groups an area takes, whether it reads the range, its own days logged, its default order, and which goals ride with it. **The source test,** exact: `ast` walks `app/services/data.py` and collects every `str` constant; none may equal (whole string, never a substring) an area's name, a field's base name, or either unit's name of a field (`weight_kg`, `weight_lb`), save the named exempt set `{"entries", "values"}`: `entries` is `AreaLogged`'s key and also the cardio and food count fields' name; `values` is `RangeTotals`' key and also the measurements count field's name. The other words `data.py` needs and a field also bears live as constants in `fields.py` and are read from there: `ROW_KEYS = ("date", "id")`, `FIELD_COLUMNS`, `PERIOD_CELLS = ("days", "days_logged")`, `TIME_GROUPS = ("day", "week", "month")`, `SUMMARY_AREAS = ("lift_sessions", "cardio", "weigh_ins", "measurements", "food")` (what_is_logged's summary counts) | The scaling rule: a later area or field is an entry, never a branch |
| R10 | **per_day's divisor** is the days with an entry in the area (`days_logged`), whatever the filters, except a field whose `per_day_by` is `"rows"`: then the days its own rows hold. `measurements.value` is the one such field today, so steps a day divide by the days with steps, never by days with only a waist | A measurement kind is its own daily series |
| R11 | **`group_by` is limited in the schema,** `min_length=1, max_length=2` on the list, as `owner_delete_feedback`'s `ids` is (`app/mcp_server.py:1467-1469`): enforced, and published as `minItems` and `maxItems`. Three groups get the schema refusal's words | `json_schema_extra` alone publishes a limit without enforcing it |
| R13 | **A set counts for every muscle its lift works: 1 for each primary muscle, 0.5 for each secondary,** both lists as free-exercise-db gives them, in `exercises.MUSCLES` (the table below). The weighting is data on the field (R9): `muscle` has a `fan` that gives each set's muscles with their weights, and `sets` and `volume_kg` are `weighted`. The rulings: (a) **the weighted set count is `sets_sum`:** `sets` takes sum, which is its count when nothing fans and the weighted count under `group_by` muscle ("triceps 9.5"); (b) **under `group_by` muscle**, a set sits in each muscle's group at its weight: a weighted field's sum and per_day multiply by it, count counts the sets that work the muscle, avg, max and min are over those sets unweighted; (c) **share** is of the weighted sums over every muscle group, so the shares sum to 100; (d) **the range `totals` count each set once,** unweighted, so the groups add up to more than the range total, and `how` says so; (e) **with no `group_by`, `muscle` is not a default field;** named in `fields`, its cell is the lift's primary muscle, null for Suitcase Carry; (f) **`filters` muscle** keeps the sets whose lift works any muscle named, at either weight; with `group_by` muscle too, **only the muscles named are groups:** the matched sets' other muscles are not shown; (g) **a muscle filter narrows the goals that ride along** to the lift goals whose lift works it, as a region filter does; (h) a set of a lift with no muscle (Suitcase Carry) sits in one group whose `muscle` is null, at 1 | The 0.5 is the "fractional" count of Pelland, Remmert, Robinson, Hinson and Zourdos, "The Resistance Training Dose-Response: Meta-Regressions Exploring the Effects of Weekly Volume and Frequency on Muscle Hypertrophy and Strength Gain", SportRxiv preprint, version 2, posted 2024-10-04 (sportrxiv.org/index.php/server/preprint/view/460, fetched on 2026-11-16). Its abstract, word for word but for straight quote marks: "weekly set volume/frequency for indirect sets was quantified as 1 for 'total,' 0.5 for 'fractional,' and 0 for 'direct.'" and "The relative evidence for the 'fractional' quantification method was strongest; therefore, this quantification method was used for the primary meta-regression models." It pooled 67 studies of 2,058 participants. free-exercise-db gives which muscles, not how much; a hand-set percent per lift would have no source. A close-grip bench (not among the 41) reads triceps 1, chest 0.5, shoulders 0.5 in free-exercise-db (`Close-Grip_Barbell_Bench_Press.json`, read on 2026-11-16 when the table was built, not read again since), so it lands triceps-heavy with chest in it |
| R12 | **Absent optional result fields are left out,** as every result does (`app/mcp_schemas.py:6-7`): `range_note`, `totals`, `more`, `note`, and `names` in an area | One rule for every answer |

## The field list (decisions 4, 5, 6, 7, 9, 10, 17)

**Module: `app/services/fields.py`, new.** Data, read by both functions. No function of the surface names an area or a field: the published text points to `what_is_logged`.

```python
@dataclass(frozen=True)
class Field:
    name: str                 # the base name: weight_kg, distance_km, speed_kmh, pace_per_km, kcal
    meaning: str              # one line, model-read: what_is_logged gives it
    quantity: str | None      # the formatting quantity a value is shown at; None for text, ids, dates, bools
    unit: str | None          # "weight", "distance", "speed", "pace" (the name and value follow the person's units)
                              # or a fixed unit word ("kcal", "min", "bpm", "%", "g"), or None
    stored: bool              # stored, or derived at read time
    totals: tuple[str, ...]   # beside count, which every field takes
    filter: bool
    group: bool
    columns: tuple[str, ...]  # the stored columns it reads, "table.column": what the tie test checks
    value: Callable[[Row], object]   # the value on a row, in stored units (kg, km, minutes)
    row_quantity: Callable[[Row], str] | None = None   # a quantity per row: measurements.value, by its unit
    ratio: tuple[str, str] | None = None   # avg as sum(first) / sum(second) over rows holding both: pace's
                                           # ("minutes", "distance_km")
    per_day_by: str = "area"  # "area": divide by days_logged; "rows": by the days this field's rows hold (R10)
    guard: Callable[[str, Query], dict | None] | None = None   # a refusal for a total in this query, or None:
                                                               # value's one-kind and daily-amount rules
    fan: Callable[[Row], list[tuple[str, float]]] | None = None   # a group field with several keys a row, each
                                                                  # with its weight: muscle (R13)
    weighted: bool = False    # under a fanned group, sum and per_day multiply by the fan's weight, and share is of
                              # those weighted sums: sets, volume_kg (R13). The range totals never weight
    match: Callable[[object], tuple[object, dict | None]] | None = None   # a filter value as sent to its stored
                              # form (a name, a list of names, a bool), or the refusal in its own words (the table)
    default: bool = True      # a default column with no fields sent; muscle: False
    key: Callable[[Row], object] | None = None      # a group's key when it is not value: food's (below)
    shown: Callable[[list[Row]], object] | None = None   # a group's cell from its rows: food's name (below)
    total_quantity: dict[str, str] = field(default_factory=dict)   # total to quantity; a total not named shows
                              # at the field's quantity (see "Steps of totals")

@dataclass(frozen=True)
class Area:
    name: str
    meaning: str
    noun: tuple[str, str]     # ("set", "sets"): the summary's words
    count_field: str          # the field that counts rows: sets, sessions or entries
    tables: tuple[str, ...]   # the tables it reads
    load: Callable[[Session, User, date | None, date | None], list[Row]]   # live rows, both ends taken, None open
    fields: tuple[Field, ...]
    groups: tuple[str, ...] = ("day", "week", "month")   # the time groups it takes; days: week and month;
                                                          # saved_foods: none
    ranged: bool = True           # saved_foods: False: every live row whatever the range; its loader counts in range
    logged_days: Callable[[Session, User, date, date], set[date]] | None = None
                                  # None: the distinct dates of the area's rows in range, before filters.
                                  # days and saved_foods give their own
    order: tuple[str, ...] = ("-date", "-id")   # the default order of rows; saved_foods: ("-times_logged", "name")
    goals: Callable[[list[dict], Query], list[dict]] = lambda numbers, query: []
                                  # the open goals' numbers in, the ones that ride out ("Goals riding with the data")

AREAS: dict[str, Area]        # in the order below; what_is_logged lists them so
INTERNAL_COLUMNS: dict[str, tuple[str, ...]]   # each area table's columns no field reads, by name
OTHER_TABLES: dict[str, str]  # every other table, with why no area reads it
ROW_KEYS, FIELD_COLUMNS, PERIOD_CELLS, TIME_GROUPS, SUMMARY_AREAS   # the shared words of R9's source test
```

A filter on a field with a `fan` matches any of its keys, and its `names` in `what_is_logged` are the keys: `muscle`'s fan gives each lift's muscles with their weights, and `exercises` has a fan too, each lift in the session at 1, so its filter and its `names` are per lift (never the joined text). A fan groups only where the field's `group` is true. An area takes no `group_by` at all when its `groups` is empty and no field of it groups: that alone gives "saved_foods takes no group_by."

`Query` is the call's checked inputs: the area, fields, filters as matched, groups, totals, order and range. A `Row` is a plain dict: `date` (the stored date), `id`, and each field's stored inputs. A loader filters `user_id` (a set through its session), `deleted_at IS NULL`, and the range with `metrics.on_or_before(date, today)`: an entry dated after today counts as today's, as in every read. It does every query in a fixed number of statements, never one a row.

A field's column name in an answer, and in `what_is_logged`, is its base name in the person's units: `weight_kg` or `weight_lb`, `estimated_max_kg` or `estimated_max_lb`, `volume_kg` or `volume_lb`, `estimate_weight_kg` or `estimate_weight_lb`, `distance_km` or `distance_mi`, `speed_kmh` or `speed_mph`, `pace_per_km` or `pace_per_mi`. A total's column is `{column}_{total}`: `kcal_per_day`, `weight_lb_max`, `sets_count`.

"Totals" below lists the totals beside `count`. `T` is sum, avg, max, min, per_day, share.

### lift_sets: "One set of a lift, in a live session."

Noun set, sets. Count field `sets`. Tables `lift_set`, `lift_session`, `exercise`. Load: the person's live sessions in range with their sets and exercises (`selectinload`, as `lifts.history`, `app/services/lifts.py:177-180`), one row a set.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day trained. | | stored | | | | `lift_session.date` |
| `session_id` | The session's id, for edit_entry with kind lift_session. | | stored | | | | `lift_set.session_id` |
| `exercise` | The lift, as list_exercises names it. | | stored | | yes | yes | `lift_set.exercise_id` to `exercise.name` |
| `category` | The lift's movement: squat, hinge, push, pull, carry, core or accessory. | | stored | | yes | yes | `exercise.category` |
| `region` | The body region the lift trains: upper, lower, core or full_body. | | derived | | yes | yes | `exercises.REGIONS[name]` |
| `muscle` | A muscle the lift works, as free-exercise-db names it, such as chest or triceps. As a column, the lift's primary muscle. Grouped by, a set counts 1 for each primary muscle and 0.5 for each secondary, in sets and volume. Null for Suitcase Carry. | | derived | | yes | yes | `exercises.MUSCLES[name]`: as a cell its weight-1 muscle; as a group, `fan` (R13). Not a default field |
| `set_number` | The set's number within its lift in the session, from 1. | `count` | stored | | | | `lift_set.set_number` |
| `weight_kg` | The weight lifted. 0 for bodyweight work. | weight, `weight_kg` | stored | avg, max, min | | | `lift_set.weight_kg` |
| `reps` | Reps in the set. | `reps` | stored | sum, avg, max, min, per_day | | | `lift_set.reps` |
| `estimated_max_kg` | Epley's estimated one-rep max: weight times 1 plus reps over 30. Null outside 1 to 10 reps or at 0 kg. | weight, `estimated_max` | derived | avg, max, min | | | `metrics.epley(weight_kg, reps)` |
| `volume_kg` | weight x reps. A 0 kg set counts 0. | weight, `volume` | derived | T | | | `weight_kg * reps`; `weighted` (R13) |
| `sets` | One for each set: count it, or sum it for sets weighted by muscle. | `count`; sum and per_day at `count_avg` | derived | sum, per_day, share | | | 1 a row; `weighted` (R13) |

### lift_sessions: "One live lift session."

Noun session, sessions. Count field `sessions`. Tables `lift_session`, `lift_set`, `exercise`.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day trained. | | stored | | | | `lift_session.date` |
| `id` | The session's id, for edit_entry with kind lift_session. | | stored | | | | `lift_session.id` |
| `exercises` | The lifts in it, in the order done. A filter keeps the sessions that hold any of them. | | derived | | yes | | its sets' names, first appearance order, joined by ", "; `fan`: each lift at 1. A filtered session's cells stay whole: its `sets`, `volume_kg` and `exercises` count every set |
| `sets` | Sets in it. | `count` | derived | sum, avg, max, min, per_day | | | its sets counted |
| `volume_kg` | weight x reps over its sets. | weight, `volume` | derived | T | | | sum of its sets' volume |
| `notes` | The person's notes. | | stored | | | | `lift_session.notes` |
| `sessions` | One for each session: count it. | | derived | share | | | 1 a row |

### cardio: "One live cardio entry."

Noun entry, entries. Count field `entries`. Tables `cardio_entry`, `exercise`.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day. | | stored | | | | `cardio_entry.date` |
| `id` | The entry's id, for edit_entry with kind cardio_entry. | | stored | | | | `cardio_entry.id` |
| `activity` | The activity, as list_exercises names it. | | stored | | yes | yes | `cardio_entry.exercise_id` to `exercise.name` |
| `category` | walk, run, cycle, row, swim, machine or other. | | stored | | yes | | `exercise.category` |
| `minutes` | How long. | min, `minutes` | stored | T | | | `cardio_entry.minutes` |
| `distance_km` | How far, when given. | distance, `km` | stored | T | | | `cardio_entry.distance_km` |
| `pace_per_km` | Minutes per distance as m:ss. Its avg is total minutes over total distance. Its min is the fastest. | pace, `pace` | derived | avg, max, min | | | `minutes / distance_km`, null without a distance. `ratio` ("minutes", "distance_km") |
| `speed_kmh` | Speed, as given or worked out from distance and minutes. | speed, `speed` | stored | max, min | | | `cardio_entry.speed_kmh` |
| `incline_pct` | Incline, when given. | %, `incline` | stored | avg, max, min | | | `cardio_entry.incline_pct` |
| `avg_hr` | Average heart rate, when given. | bpm, `bpm` | stored | avg, max, min | | | `cardio_entry.avg_hr` |
| `machine_kcal` | The machine's or watch's calories, when given. | kcal, `kcal` | stored | T | | | `cardio_entry.machine_kcal` |
| `estimated_kcal` | Spotter's estimate: MET x 3.5 x body weight / 200 x minutes. | kcal, `kcal` | stored | T | | | `cardio_entry.estimated_kcal` |
| `estimate_weight_kg` | The body weight the estimate used. | weight, `bodyweight_derived` | stored | | | | `cardio_entry.estimate_weight_kg` |
| `notes` | The person's notes. | | stored | | | | `cardio_entry.notes` |
| `entries` | One for each entry: count it. | | derived | per_day, share | | | 1 a row |

### weigh_ins: "One live weigh-in: at most one a day."

Noun weigh-in, weigh-ins. Count field `weigh_ins`. Table `bodyweight`.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day. | | stored | | | | `bodyweight.date` |
| `id` | The weigh-in's id, for edit_entry with kind bodyweight. | | stored | | | | `bodyweight.id` |
| `weight_kg` | Body weight. | weight, `weigh_in` | stored | avg, max, min | | | `bodyweight.kg` |
| `weigh_ins` | One for each weigh-in: count it. | | derived | | | | 1 a row |

### measurements: "One live measurement: at most one a kind a day."

Noun measurement, measurements. Count field `values`. Table `measurement`.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day. | | stored | | | | `measurement.date` |
| `id` | The measurement's id, for edit_entry with kind measurement. | | stored | | | | `measurement.id` |
| `kind` | waist, hips, chest, arm, thigh, steps, sleep_hours, resting_hr or water_ml. | | stored | | yes | yes | `measurement.kind` |
| `value` | The value, in unit. Its totals need one kind. sum, per_day and share only for steps, sleep_hours and water_ml. Its per_day divides by the days with that kind. | the kind's, `rules.UNIT_QUANTITIES[unit]` | stored | T | | | `measurement.value` |
| `unit` | The kind's unit: cm, steps, h, bpm or ml. | | stored | | | | `measurement.unit` |
| `values` | One for each measurement: count it. | | derived | | | | 1 a row |

`value`'s quantity comes from `rules.UNIT_QUANTITIES` (`app/rules.py:111`): cm at 0.1, steps whole, h at 0.01, bpm whole, ml whole. Its totals need one kind: a `kind` filter of one name, or `group_by` kind. sum, per_day and share need daily-amount kinds only: steps, sleep_hours, water_ml (`goals.DAILY_AMOUNTS`, `app/services/goals.py:113`). Both rules are `value`'s `guard`, its quantity is its `row_quantity`, and its per_day divides by the days with that kind (`per_day_by` "rows", R10).

### food: "One live food entry."

Noun entry, entries. Count field `entries`. Tables `food_entry`, `saved_food`, `usda_food`.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day eaten. | | stored | | | | `food_entry.date` |
| `id` | The entry's id, for edit_entry with kind food_entry. | | stored | | | | `food_entry.id` |
| `meal` | breakfast, lunch, dinner or snack. | | stored | | yes | yes | `food_entry.meal` |
| `description` | What was eaten, as written. | | stored | | | | `food_entry.description` |
| `food` | The saved or USDA food's name, else the description: what group_by food groups on. | | derived | | | yes | see below |
| `kcal` | Calories, whole. | kcal, `kcal` | stored | T | | | `int(food_entry.kcal)`, as `food.entry_dict` reads it (`app/services/food.py:84-87`) |
| `source` | Who gave the number: you_said, saved_food, usda or claude_estimate. | | stored | | yes | | `food_entry.source` |
| `estimated` | true when the number is an estimate. | | stored | | yes | | `food_entry.estimated` |
| `saved_food_id` | The saved food it came from, or null. | | stored | | | | `food_entry.saved_food_id` |
| `servings` | Servings of the saved food. | `servings` | stored | sum, avg | | | `food_entry.servings` |
| `grams` | Grams of the USDA food. | g, `grams` | stored | sum, avg | | | `food_entry.grams` |
| `usda_fdc_id` | The USDA food's id, or null. | | stored | | | | `food_entry.usda_fdc_id` |
| `entries` | One for each entry: count it. | | derived | per_day, share | | | 1 a row |

`food`: the key is the saved food's id when there is one, else the USDA id, else the description with spaces folded and case ignored (`" ".join(d.split()).casefold()`). The name shown is the saved food's name (a deleted one's too), else the cached USDA description (`usda_food.description`), else the description as written on the group's latest entry.

### saved_foods: "One live saved food. The range counts its use."

Noun saved food, saved foods. Count field `foods`. Tables `saved_food`, `food_entry`. `ranged` False: every live saved food is a row whatever the range; `times_logged` counts its live entries in the range. `logged_days`: the days in range with a food entry of a saved food. `groups` empty and no field that groups: no `group_by`. `order` ("-times_logged", "name"): most used first, then by name.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `id` | The saved food's id, for log_food. | | stored | | | | `saved_food.id` |
| `name` | Its name. | | stored | | | | `saved_food.name` |
| `serving_label` | What one serving is. | | stored | | | | `saved_food.serving_label` |
| `kcal_per_serving` | Calories in one serving. | kcal, `kcal_per_serving` | stored | avg, max, min | | | `saved_food.kcal_per_serving` |
| `grams_per_serving` | Grams in one serving, or null. | g, `grams` | stored | | | | `saved_food.grams_per_serving` |
| `times_logged` | Live entries of it in the range. | `count` | derived | sum, max | | | its food entries counted |
| `last_logged` | The day of its latest live entry, or null. | | derived | | | | as `food._usage` (`app/services/food.py:460-467`) |
| `foods` | One for each saved food: count it. | | derived | | | | 1 a row |

### days: "One row a day of the range, empty days included."

Noun day, days. Count field `days`. Its loader reads the other areas' loaders for the range and gives one row a day, empty days included. `logged_days`: the days with any entry. `groups` ("week", "month"), no field group.

| Field | Meaning | Unit | Stored or derived | Totals | Filter | Group | From |
|---|---|---|---|---|---|---|---|
| `date` | The day. | | derived | | | | each day of the range |
| `lift_sessions` | Lift sessions that day. | `count` | derived | sum, avg, max | | | |
| `lift_sets` | Sets that day. | `count` | derived | sum, avg, max | | | |
| `volume_kg` | weight x reps that day. | weight, `volume` | derived | sum, avg, max | | | |
| `cardio_entries` | Cardio entries that day. | `count` | derived | sum, avg, max | | | |
| `cardio_minutes` | Cardio minutes that day. | min, `minutes` | derived | sum, avg, max | | | |
| `food_entries` | Food entries that day. | `count` | derived | sum, avg, max | | | |
| `kcal` | Calories that day, or null with no food. | kcal, `kcal` | derived | sum, avg, max, min | | | |
| `weight_kg` | The day's weigh-in, or null. | weight, `weigh_in` | derived | avg, max, min | | | |
| `measurements` | Measurements that day. | `count` | derived | sum | | | |
| `days` | One for each day: count it. | | derived | | | | 1 a row |

Every day of a time group is in range by construction, so `days`' rows are the range's days, and a week group's `days_logged` is its days with any entry.

### What no field reads

`INTERNAL_COLUMNS`, by name: every entry table's `user_id`, `dedupe_key`, `logged_by`, `created_at`, `updated_at`, `deleted_at`; `lift_set.id`; `saved_food.user_id`, `name_key`, `created_at`, `updated_at`, `deleted_at`; `exercise.id`, `kind`, `met`, `compendium_code`; `usda_food.fdc_id` (read through `food_entry.usda_fdc_id`), `data_type`, `kcal_per_100g`, `fetched_at`.

`OTHER_TABLES`, each with its reason: `user` (settings: `whoami`), `goal` (`get_goals`; open goals ride along), `routine` (`get_routines`), `call_log`, `write_key`, `entry_version`, `feedback`, `usda_search` (internal).

**The tie test** (`tests/test_data.py`): for every table in `Base.metadata.sorted_tables`, either an area's `tables` holds it or `OTHER_TABLES` does; for every table an area reads, every column is in some field's `columns` or in `INTERNAL_COLUMNS[table]`; every name in `columns` and `INTERNAL_COLUMNS` is a real column. A column added to a person's table fails the test until it is listed. It reads the tables from the metadata, never from `models.user_tables()`, which misses `lift_set`.

### The scaling rule

Areas and fields are data in `AREAS`. Neither function, nor its published entry, nor a result model names one. A later area (a format's parts with split times, say) is a table, its loader and its field entries. A later field (protein, say) is a column and one `Field`. Neither changes an input or a result model.

**The test** (`tests/test_data.py`): with `monkeypatch`, register a throwaway area, `test_parts`, whose loader returns three fixed rows (a `part` text field that groups and filters; a `seconds` number field with T; a `split` field whose avg is a `ratio` of `seconds` over a `metres` field; a `guarded` field whose `guard` refuses sum unless `part` is filtered to one name; a `zone` field with a `fan` that gives each row two keys, one at 1 and one at 0.5, and `seconds` marked `weighted`), and a throwaway field `kcal_tenth` on `food`. Then `what_is_logged` lists both with their meanings, `get_data` reads `test_parts` with a filter, a group and every total (the ratio avg against a hand-worked figure, the guard's refusal and its pass, and `group_by` zone giving each zone's weighted `seconds_sum` and shares summing to 100, while the range totals count each row once), and `food` with `kcal_tenth_sum`. The owner's `tools/list` is byte for byte the same before and after, and so is every model in `RESULTS`.

## Body region and main muscle for the 41 lifts (decision 17)

`app/exercises.py` gains `MUSCLES: dict[str, dict[str, float]]` (each lift's muscles with their weight: 1 for each in the table's primary column, 0.5 for each in its secondary column; empty for Suitcase Carry) and `REGIONS: dict[str, str]`, beside `LIFTS`. Every lift but Suitcase Carry has exactly one primary muscle, and its region is worked from it. Checked on 2026-11-16 against the table below: 40 rows with one primary each, every muscle among the 17 names, no muscle both primary and secondary in a row. `LIFTS`, `rows()` and `sync` do not change: `sync` builds `Exercise(**row)` (`app/exercises.py:108`), and `ExerciseItem` is pinned by hash (`tests/test_feedback.py:68-90`), so region and muscle are never a column and never in `list_exercises`. A test holds `set(REGIONS) == set(MUSCLES) == {name for name, _ in LIFTS}`, every region in `upper`, `lower`, `core`, `full_body`, every muscle in free-exercise-db's 17 names (abdominals, abductors, adductors, biceps, calves, chest, forearms, glutes, hamstrings, lats, lower back, middle back, neck, quadriceps, shoulders, traps, triceps), every weight 1 or 0.5, exactly one weight of 1 a lift but Suitcase Carry, the table's muscles exactly, and each region equal to the rule below applied to the primary muscle, save the two named exceptions.

Each region is worked from the primary muscles of the lift's free-exercise-db entry (github.com/yuhonas/free-exercise-db, The Unlicense, read on 2026-11-16, each entry at `exercises/<entry>.json`). The rule: lower is quadriceps, hamstrings, glutes, calves, adductors, abductors and lower back; upper is chest, shoulders, lats, middle back, traps, biceps, triceps, forearms and neck; core is abdominals. Lower back counts as lower, so Deadlift (primary: lower back) reads lower, as a hinge. Two named exceptions: Farmer's Carry (primary forearms, secondaries across every region) and Suitcase Carry (no entry in free-exercise-db) are full_body: a carry is grip, legs and trunk at once. Match: exact is the same name; same is the same movement and equipment under another name; near says what differs in the source file. The 41 stay as they are: no two map to the same entry. Equipment pairs (Bench Press and Dumbbell Bench Press, Romanian Deadlift and its dumbbell form, Overhead Press and Dumbbell Shoulder Press) and Hip Thrust beside Glute Bridge stay apart, since their weights are not comparable and merging would mix their bests.

| Lift | free-exercise-db entry | Match | Primary muscles (1 a set) | Secondary muscles (0.5 a set) | Region |
|---|---|---|---|---|---|
| Back Squat | `Barbell_Full_Squat` | same | quadriceps | calves, glutes, hamstrings, lower back | lower |
| Front Squat | `Front_Barbell_Squat` | same | quadriceps | calves, glutes, hamstrings | lower |
| Goblet Squat | `Goblet_Squat` | exact | quadriceps | calves, glutes, hamstrings, shoulders | lower |
| Leg Press | `Leg_Press` | exact | quadriceps | calves, glutes, hamstrings | lower |
| Bulgarian Split Squat | `Split_Squat_with_Dumbbells` | same | quadriceps | glutes, hamstrings | lower |
| Walking Lunge | `Bodyweight_Walking_Lunge` | same | quadriceps | calves, glutes, hamstrings | lower |
| Bodyweight Squat | `Bodyweight_Squat` | exact | quadriceps | glutes, hamstrings | lower |
| Step-Up | `Dumbbell_Step_Ups` | same | quadriceps | calves, glutes, hamstrings | lower |
| Deadlift | `Barbell_Deadlift` | same | lower back | calves, forearms, glutes, hamstrings, lats, middle back, quadriceps, traps | lower |
| Romanian Deadlift | `Romanian_Deadlift` | exact | hamstrings | calves, glutes, lower back | lower |
| Dumbbell Romanian Deadlift | `Stiff-Legged_Dumbbell_Deadlift` | near | hamstrings | glutes, lower back | lower |
| Hip Thrust | `Barbell_Hip_Thrust` | same | glutes | calves, hamstrings | lower |
| Glute Bridge | `Barbell_Glute_Bridge` | same | glutes | calves, hamstrings | lower |
| Kettlebell Swing | `One-Arm_Kettlebell_Swings` | near | hamstrings | calves, glutes, lower back, shoulders | lower |
| Bench Press | `Barbell_Bench_Press_-_Medium_Grip` | same | chest | shoulders, triceps | upper |
| Incline Dumbbell Press | `Incline_Dumbbell_Press` | exact | chest | shoulders, triceps | upper |
| Dumbbell Bench Press | `Dumbbell_Bench_Press` | exact | chest | shoulders, triceps | upper |
| Overhead Press | `Standing_Military_Press` | same | shoulders | triceps | upper |
| Dumbbell Shoulder Press | `Dumbbell_Shoulder_Press` | exact | shoulders | triceps | upper |
| Push-Up | `Pushups` | same | chest | shoulders, triceps | upper |
| Dip | `Parallel_Bar_Dip` | same | triceps | chest, shoulders | upper |
| Pull-Up | `Pullups` | same | lats | biceps, middle back | upper |
| Lat Pulldown | `Wide-Grip_Lat_Pulldown` | same | lats | biceps, middle back, shoulders | upper |
| Barbell Row | `Bent_Over_Barbell_Row` | same | middle back | biceps, lats, shoulders | upper |
| Dumbbell Row | `One-Arm_Dumbbell_Row` | same | middle back | biceps, lats, shoulders | upper |
| Seated Cable Row | `Seated_Cable_Rows` | same | middle back | biceps, lats, shoulders | upper |
| Inverted Row | `Inverted_Row` | exact | middle back | lats | upper |
| Face Pull | `Face_Pull` | exact | shoulders | middle back | upper |
| Farmer's Carry | `Farmers_Walk` | same | forearms | abdominals, glutes, hamstrings, lower back, quadriceps, traps | full_body |
| Suitcase Carry | none | no match | none | none | full_body |
| Plank | `Plank` | exact | abdominals | none | core |
| Side Plank | `Side_Bridge` | same | abdominals | shoulders | core |
| Dead Bug | `Dead_Bug` | exact | abdominals | none | core |
| Hanging Knee Raise | `Hanging_Leg_Raise` | near | abdominals | none | core |
| Pallof Press | `Pallof_Press` | exact | abdominals | chest, shoulders, triceps | core |
| Leg Curl | `Lying_Leg_Curls` | same | hamstrings | none | lower |
| Leg Extension | `Leg_Extensions` | same | quadriceps | none | lower |
| Calf Raise | `Standing_Calf_Raises` | same | calves | none | lower |
| Dumbbell Curl | `Dumbbell_Bicep_Curl` | same | biceps | forearms | upper |
| Triceps Pushdown | `Triceps_Pushdown` | exact | triceps | none | upper |
| Lateral Raise | `Side_Lateral_Raise` | same | shoulders | none | upper |

17 lower, 17 upper, 5 core, 2 full_body: 41. The entry ids also serve the later switch to the full list. Push against pull is `group_by` category: push 7 lifts, pull 7; the accessories (curls, pushdowns, raises, leg curls) group under accessory there, and under the muscles they work by `group_by` muscle, which is the split that fits any routine. A body map on a page, each muscle coloured by its weighted sets, reads the same 17 names: the pages are not in this work. free-exercise-db's 17 names are as fine as this goes: one chest, one shoulders, no front or rear delts. A finer split needs another source, later.

## get_data's inputs

Every input but `area` is optional, and null is taken as left out (R2). A blank or spaces-only text is taken as left out, as the forms send it.

| Input | Type | Default | Range | Description, in full |
|---|---|---|---|---|
| `area` | string | required | an area of `AREAS` | "An area, as what_is_logged names it, such as lift_sets or food." |
| `fields` | array of strings | every field but the count field and `muscle`, in list order | the area's fields, either unit's name (R3) | "The fields to show, as what_is_logged names them. Leave out for every field. Not with group_by." |
| `filters` | object, `dict[str, Any] | None` | none | the area's filter fields, each checked by the service, so a bad value gets the refusal table's words: a typed union would answer it in the schema's | "Only rows that match, by a filter field of what_is_logged: a name, a list of names, or true or false, such as {\"exercise\": \"Back Squat\"}." |
| `from_date` | string | `to_date` less 27 days, or `log.oldest_day(user)` when that is later | YYYY-MM-DD, from `log.oldest_day(user)` | "The first day, YYYY-MM-DD. Leave out with to_date for the last 28 days." |
| `to_date` | string | today | YYYY-MM-DD, not after today | "The last day, YYYY-MM-DD. Leave out for today." |
| `group_by` | array of strings | none | 1 to 2 items: `min_length=1, max_length=2` (R11) | "One or two groups: day, week, month, or a field what_is_logged marks as a group. Each row is then one group." |
| `totals` | object, `dict[str, Any] | None`, checked by the service | none, or `{count_field: ["count"]}` with `group_by`. `{}` is taken as left out | a field's allowed totals | "By field, as what_is_logged allows: sum, avg, max, min, count, per_day or share, such as {\"kcal\": [\"sum\", \"per_day\"]}." |
| `order` | string | newest first; groups by a field, largest first | a column of the answer, a leading minus for largest first | "A column of the answer to sort by, smallest first, or with a minus sign first for largest first, such as -kcal_sum. Leave out for newest first, or largest first for groups by a field." |
| `limit` | integer, `whole_in(1, 300)` | 100 | 1 to 300 | "The most rows: 1 to 300. Leave out for 100." |
| `cursor` | string | none | a `more` this function gave | "more from the last answer, as it came, with the same query." |

Python parameter order is the table's order. Filter values: a name, or a list of names (any of them), for a text field; `true` or `false` for `estimated`. Names are matched as their own functions match them: `exercise` and `exercises` by `rules.match_exercise` over the lifts, `activity` over the cardio activities, `kind` by `rules.match_kind`, `meal` by `rules.match_meal`, and `category`, `region`, `muscle`, `source` by case and outer spaces against their lists.

### The refusals

Every problem of a call at once, through `rules.combine`. Nothing is read when a call is refused. The codes are the ones in use (`app/rules.py`, `docs/contracts/inputs.md` D11); `cursor_changed` is new. The examples are on a kg account; on a lb account each list of fields is in lb.

| When | Code | Message | Fix |
|---|---|---|---|
| An area not in the list | `invalid_field` | "area 'lifts' is not an area Spotter reads." | "Use one of: lift_sets, lift_sessions, cardio, weigh_ins, measurements, food, saved_foods, days." then " The nearest is lift_sets." when `difflib` finds one at 0.6 |
| `area` blank or spaces only | `missing_field` | "area is missing." | "Give area, as what_is_logged names it." |
| `fields` an empty list | `invalid_field` | "fields holds no fields." | "Name at least one field, or leave fields out for every field." |
| A field not in the area, in `fields` or `totals` | `invalid_field` | "'weight' is not a field of lift_sets." | "lift_sets takes: {its fields}." then " The nearest field is weight_kg." at `mcp_schemas.NEAR_CUTOFF` |
| `fields` with `group_by` | `invalid_field` | "fields and group_by do not go together: with group_by each row is a group." | "Leave fields out with group_by, and name in totals what to work out for each group." |
| A filter the area does not take | `invalid_field` | "'colour' is not a filter of lift_sets." | "lift_sets filters on: exercise, category, region, muscle." |
| Any filter, to an area with none | `invalid_field` | "weigh_ins takes no filters." | "Leave filters out for weigh_ins." |
| A filter value of the wrong shape, or an empty list | `invalid_field` | "filters.exercise holds no names." / "filters.exercise takes a name or a list of names, not 5." / "filters.estimated takes true or false, not 'yes'." | "Send one name or a list of names, or leave the filter out." / the same / "Send estimated as true or false, or leave it out." |
| A totals value that is not a list | `invalid_field` | "totals.kcal takes a list of totals, not 'sum'." | "Send totals.kcal as a list, such as [\"sum\"]." |
| An exercise or activity not on the list | `unknown_exercise` | `rules.unknown_exercise` with where "filters.{field}: ", the field as sent: "filters.activity: 'Basketball' is not on the exercise list." | its own, with `closest` |
| A measurement kind | `invalid_kind` | `rules.match_kind`'s | its own |
| A meal | `invalid_field` | `rules.match_meal`'s with where "filters": "filters: meal 'brunch' is not a meal." | "Use one of: breakfast, lunch, dinner, snack." in place of its own, which names the clock (`app/rules.py:464-471`) |
| A category, region, muscle or source not in its list | `invalid_field` | "region 'legs' is not a region." / "muscle 'pecs' is not a muscle." | "Use one of: upper, lower, core, full_body." / "Use one of: abdominals, abductors, adductors, biceps, calves, chest, forearms, glutes, hamstrings, lats, lower back, middle back, neck, quadriceps, shoulders, traps, triceps." |
| A group the area does not take | `invalid_field` | "'colour' is not a group of lift_sets." | "lift_sets groups by: day, week, month, exercise, category, region, muscle." |
| Two of day, week and month | `invalid_field` | "group_by holds week and month: one of day, week and month at most." | "Keep one of day, week and month in group_by." |
| A group named twice | `invalid_field` | "group_by names exercise twice." | "Name each group once." |
| `group_by` on saved_foods | `invalid_field` | "saved_foods takes no group_by." | "Leave group_by out for saved_foods." |
| A total not in the seven | `invalid_field` | "'mean' is not a total." | "Use: sum, avg, max, min, count, per_day, share." |
| A total a field does not take | `invalid_field` | "weight_kg takes no sum." | "weight_kg takes: count, avg, max, min." |
| `share` without `group_by` | `invalid_field` | "share needs group_by: it is a group's part of the range." | "Send group_by with share, or leave share out." |
| An empty list of totals | `invalid_field` | "totals.kcal holds no totals." | "Name at least one total for kcal, or leave it out." |
| `value` totals over more than one kind | `invalid_field` | "value holds more than one kind, so its totals mix units." | "Filter on one kind, such as {\"kind\": \"waist\"}, or send group_by kind." |
| sum, per_day or share of a kind that is not a day's amount, with no `group_by` kind | `invalid_field` | "waist takes no sum: it is not a day's amount." | "Use avg, max or min for waist. sum and per_day are for steps, sleep_hours and water_ml." |
| sum, per_day or share of `value` with `group_by` kind, and no kind filter or a kind filter naming a kind that is not a day's amount | `invalid_field` | "value's sum needs a kind filter of steps, sleep_hours or water_ml: grouped by kind, it could take in a kind that is not a day's amount." | "Filter kind to steps, sleep_hours or water_ml, or use avg, max or min." |
| `order` names no column of the answer (the only row an `order` gets) | `invalid_field` | "order 'kcal' is not a column of this answer." | "Order by one of: {the columns}, or with a minus sign first, such as -kcal_sum." |
| A date not YYYY-MM-DD | `invalid_date` | "from_date '20261116' is not a date." | "Give from_date as YYYY-MM-DD, or leave it out." |
| `to_date` after today | `date_in_future` | "to_date 2026-12-01 is after today (2026-11-16)." | "Give to_date as today or earlier, or leave it out for today." |
| `from_date` after `to_date` | `invalid_field` | "from_date 2026-11-10 is after to_date 2026-11-01." | "Give from_date on or before to_date." |
| `from_date` before `log.oldest_day` | `date_too_old` | "from_date 2020-01-01 is before 2024-11-14, the oldest day this account can hold." | "Give a date from 2024-11-14 to today, or leave from_date out." |
| A cursor Spotter did not give | `invalid_field` | "cursor is not one Spotter gave." | "Send more from the last get_data answer as it came, or leave cursor out to start from the first rows." |
| A cursor sent with another query | `cursor_changed` | "This cursor is for another query." | "Send the cursor with the same area, fields, filters, from_date, to_date, group_by, totals and order as the call that gave it, or leave cursor out to start again." |

A wrong type, a `limit` outside 1 to 300, three groups ("group_by holds 3 items: it takes 1 to 2." / "Send 1 to 2 items in group_by."), and an unknown top-level key are the schema refusal's, in its words (`app/mcp_server.py:488-549`). A null is never refused: it is left out (R2). Every message and fix above passed `tests/text_rules.py` on the MESSAGE surface on 2026-11-16.

## The answers

### Rows, groups and totals

- **No `group_by`:** one row a row of the area, its cells the fields asked, in list order. With `totals`, `totals` holds one row of range totals, every total but share.
- **With `group_by`:** one row a group. Its cells: each group's key, then for a week or month group `days` (its days in range) and `days_logged` (its days with an entry in the area), then each total asked, in the order sent. A day's key is the date; a week's the Monday, YYYY-MM-DD; a month's YYYY-MM. A time key's column is `day`, `week` or `month`; a field key's is its field's column name in the person's units; the period cells' are `days` and `days_logged`. With no `totals`, the count field's count. `totals` holds the range totals of the same, share left out.
- **Grouped by muscle (R13):** a weighted field's sum and per_day are weighted, count counts the sets that work the muscle, avg, max and min are unweighted, and the range `totals` count each set once. With a muscle filter, only the muscles named are groups.
- **Time groups list every period of the range,** empty ones too: count 0 and every other total null. Field groups list only the values present. With a time group and a field group, an empty period is one row with the field key null.
- **Default order:** rows by the area's `order`, newest first (date, then id, descending), saved_foods most used first; time groups newest first; a field group by its first total column, largest first, ties by key. With two groups, the first sent sorts first by its own default, the second within it. With `order`, the rows sort by that column, nulls last, the default order breaking ties. A pace column sorts, and takes its max and min, by its minutes, never as text.

### Totals semantics

Every total is worked from stored numbers (kg, km, minutes), unrounded, then converted once to the person's units and rounded once at its quantity's shown step.

| Total | Meaning |
|---|---|
| `sum` | The sum of the rows' values in the group or range, nulls left out. Null when no row holds a value |
| `avg` | Their mean, the same way, shown at the step "Steps of totals" gives |
| `max`, `min` | The largest and smallest. For a pace, min is the fastest |
| `count` | The rows with a value. 0, never null. Every field takes it |
| `per_day` | The sum over the days with an entry in the area, in the range or in the group's week or month: `days_logged`. Filters do not change that divisor: a meal's kcal a day divides by the days with food, as `get_trends` does (`app/services/trends.py:111-122`). A field with `per_day_by` "rows" divides by the days its own rows hold instead (R10). Shown at the step "Steps of totals" gives |
| `share` | A group's whole percent of the sum of the same field over every group, as `how` says: its sum, which for a count field is 1 a row, and under `group_by` muscle the weighted sum (R13), so a fanned field's shares still sum to 100. Rounded by largest remainder, so every group's shares sum to 100; a tie in remainder goes to the larger value, then the earlier group in default order. Worked over every group before order, limit and paging (R8). With two groups, a share is of the range total, not of its period. Groups with a null sum have a null share |
| A pace's `avg` | Total minutes over total distance, over the rows with a distance: never an average of paces |

Refused where it means nothing: each field takes only the totals its row above names, plus count. Text, ids and dates take count alone.

**Steps of totals** (`Field.total_quantity`). A total not named here shows at the field's own quantity.

| Field | Totals | Quantity |
|---|---|---|
| lift_sets `sets` | sum, per_day | `count_avg` (0.1): a weighted sum reads 9.5 (R13) |
| lift_sets `reps`, lift_sessions `sets`, saved_foods `times_logged`, the counts of `days` | avg, per_day | `count_avg` |
| the count fields `sessions` and `entries` | per_day | `count_avg` |
| `avg_hr` | avg | `count_avg` |
| every kcal field (`kcal`, `machine_kcal`, `estimated_kcal`, `kcal_per_serving`) | every total | its kcal quantity: whole, as `get_trends` gives a day's average (`app/services/trends.py:111-122`) |
| measurements `value` | every total | its kind's, through `row_quantity` |

**Smaller rulings.**
- `days`' `first`, `last` and 28-day counts in `what_is_logged` are its days with any entry.
- `first` and `last` are stored dates; the 28-day windows count by `metrics.on_or_before`, as the range does.
- A `totals` of share alone leaves the range `totals` out (R12): share has no range total.
- `last_logged` is all time while `times_logged` is in range, as `food._usage` gives it (`app/services/food.py:460-467`): on purpose.
- `value`'s `count` is never guarded: a count of measurements mixes no units.

### Units and rounding (decision 13)

- `formatting.py` gains `shown_number(value, quantity, *, units=None)`, returning an int or a float: `_shown(value, quantity, False, units)` as a number, an int when whole. A result cell is never `100.0`. For a quantity kept in minutes it raises, as `rounded` does: a pace is text.
- Two quantities join `_TABLE`: `minutes` ("0.1", "min", 1) and `volume` ("0.1", "kg", 2), and `_SHOWN_IN["lb"]` gains `volume` ("0.1", "lb", "0.01"), so `WEIGHT_QUANTITIES` holds it by itself: eight rows, where units R4 counts seven (`docs/contracts/units.md:89`). `minutes` is a number of minutes, so it stays out of `_MINUTES` (`app/formatting.py:122`), which turns minutes into seconds. No other row changes. Two tests pin the quantity table and move: `tests/test_formatting.py:37` (`set(f.STEP)`: its sets gain `minutes` and `volume`) and `:149` (`RULES`: rows for both), with `test_step`'s rows.
- The shown steps are units decision 9's (`docs/contracts/units.md:67`): a set and a weigh-in 0.05 lb (0.01 kg); an estimated max and a body weight the estimate used 0.1 lb (0.1 kg); km and mi 0.01; km/h and mph 0.1; a pace m:ss per km or per mi through `formatting.show(minutes_per_km, "pace", unit=False, units=units)`. A volume 0.1 in either.
- On a lb account 97.5 kg reads 214.95 (`weight_lb`); 100 kg × 5 reads volume 1102.3 lb. Measured on 2026-11-16 with `formatting.show`.

### `what_is_logged`'s answer (decision 15)

`WhatIsLoggedResult`, top level forbids extras.

| Field | Type | Description, in full |
|---|---|---|
| `today` | str | "Today's date for the person, YYYY-MM-DD." |
| `field_columns` | list[str] | "What each list in an area's fields holds, in order." Always `["field", "meaning", "unit", "from", "totals", "filter", "group", "filled"]` |
| `areas` | list[AreaLogged] | "Every area get_data reads, with what the person has in it." |
| `goals` | list[OpenGoal] | "Every open goal, with its plan line." |
| `targets` | Targets (`app/mcp_schemas.py:367-370`) | "The person's targets.", `whoami`'s own words (`app/mcp_schemas.py:412`) |
| `how` | str | "How get_data works out each total." |
| `summary` | str | "What is logged, in one line, ready to quote." |
| `note` | str, null default | "Present when nothing is logged yet." |

`AreaLogged`: `area` "The area's name: get_data takes it as area."; `meaning` "What one row of the area is."; `entries` int "Rows the person has in it, deleted ones left out."; `first` and `last` (str or null) "The first row's date, YYYY-MM-DD, or null with none." / "The last row's date, YYYY-MM-DD, or null with none."; `last_28d` "Rows in the 28 days ending today."; `prior_28d` "Rows in the 28 days before those."; `fields` list[list[str | int | float | bool | None]] "The area's fields, one list each, in field_columns' order."; `names` dict[str, list[str]] or null "The names logged in the area, by field, most used first: every exercise logged, say."

A field's list: its name in the person's units, its meaning, its unit word in the person's units (kg or lb, km or mi, km/h or mph, "m:ss per km" or "m:ss per mi", or the fixed word, or null), "stored" or "derived", its totals as one text, count first, joined by ", " ("count, avg, max, min"), filter and group as true or false, and `filled`: the person's rows with a value, all time (0 when never logged). `names`: for each text field that filters or groups, its values logged all time (a fanned field's keys, so each lift's name for `exercises`), most rows first, at most 50 a field; null for an area with none. For `muscle`, `names` holds the muscles of the lifts logged, by weighted sets all time, most first, and `filled` the sets whose lift has a muscle (every set but Suitcase Carry's). For `saved_foods`, `first`, `last` and the 28-day counts are its foods' created dates in the person's zone. For `days`, `entries` is the days with any entry.

`OpenGoal` (R4), the same model in both answers: `goal_id` "The goal's id, as get_goals gives it."; `label` "The goal in a few words, in the person's units, as get_goals gives it."; `status` "reached, on track, behind or not enough data, as get_goals gives it."; `target_date` "The date to reach it by, YYYY-MM-DD."; `plan_line` (str or null) "Where the goal stands against its plan line, ready to say as written. Null with nothing to compare." Each value is the `GoalNumbers` value of that name, from `goals.load_numbers`, `goal_numbers(open_goals, loaded, today, units=units)` and `rounded_numbers`, as `get_goals` reads them (`app/services/goals.py:958-959`), so the two never disagree.

`how`, in full: "sum, avg, max and min are over the rows' values, nulls left out, and null when no row holds one. count counts the rows with a value. per_day is the sum over the days with an entry in the area, in the range or in the group's week or month: days_logged says how many. For a measurement's value, it is the days with that kind. share is a group's whole percent of the sum over every group, and the shares sum to 100. Grouped by muscle, a set counts in each muscle its lift works, 1 for a primary and 0.5 for a secondary, in the sums, per_day and share, and count counts the sets that work it, so the groups add up to more than the range totals, which count each set once. A pace's avg is total minutes over total distance. Weeks run Monday to Sunday and months are calendar months: a week or month group gives its days in range and its days logged. Totals are worked out from the stored numbers, then shown in the person's units. Deleted entries are never counted."

`summary`: "{entries} entries in {areas} areas, from {first day} to {last day}. {n} open goals." in `formatting.day` form, the entries and the areas counted over `fields.SUMMARY_AREAS` only, which holds lift_sessions, cardio, weigh_ins, measurements and food (a set is in its session, a saved food is no entry), the one forms "1 entry", "1 area" and "1 open goal"; with no open goal the last sentence is "No open goals." `note`, on an account with nothing: "Nothing is logged yet: every area is empty." `targets` is `whoami`'s, as R5 says; no other setting is in the answer.

### `get_data`'s answer (decision 12)

`GetDataResult`, top level forbids extras, `populate_by_name` for `from`.

| Field | Type | Description, in full |
|---|---|---|
| `area` | str | "The area read." |
| `from` (`from_`) | str | "The range's first day, YYYY-MM-DD." |
| `to` | str | "The range's last day, YYYY-MM-DD." |
| `range_note` | str, null default | "Present when from_date was left out: say which range this is." |
| `days_in_range` | int | "Days from the first to the last, both counted." |
| `days_logged` | int | "Days in the range with an entry in the area, whatever the filters." |
| `columns` | list[str] | "What each row holds, in order. A name carries its unit, such as weight_lb." |
| `rows` | list[list[str | int | float | bool | None]] | "The rows, one list each, in columns' order. Null where a field holds nothing." |
| `totals` | RangeTotals, null default | "The totals asked for, over the whole range." |
| `more` | str, null default | "Present when more rows match: send it as cursor, with the same query, for the next rows." |
| `goals` | list[OpenGoal] | "The open goals this query is about, such as the filtered lift's, each with its plan line." |
| `targets` | Targets, always present (R5) | "The person's targets.", `whoami`'s own words |
| `summary` | str | "What came back, in one line, ready to quote." |
| `note` | str, null default | "Present when nothing is in range, or the rows stop at the size limit." |

`RangeTotals`: `columns` list[str] "Each total's name: the field, then the total, such as kcal_sum."; `values` list of cells "The totals over the whole range, in columns' order."

The words:

| Text | When | Words |
|---|---|---|
| `range_note` | `from_date` left out | "No range was given: this is the last 28 days, 20 Oct to 16 Nov." With `to_date` sent: "No from_date was given: this is the 28 days ending 16 Nov." |
| `summary` | all rows here | "108 sets from 20 Oct to 16 Nov, all here." Groups: "4 groups from 20 Oct to 16 Nov, all here." |
| `summary` | a page | "108 sets from 20 Oct to 16 Nov, rows 1 to 100 here. Send more for the rest." |
| `note` | nothing in range, entries before it | "Nothing in lift_sets from 20 Oct to 16 Nov. The last one is on Mon 3 Aug." |
| `note` | nothing ever | "Nothing is logged in measurements yet." |
| `note` | cut at the size limit (R7) | "The rows stop at the size limit: send more for the rest." |

Days in `formatting.days` and `formatting.day` form. The dates are examples. Every summary, note and line above, every field meaning and `how` passed `tests/text_rules.py` on the SUMMARY surface on 2026-11-16, and every description in this contract on the CLAUDE surface.

**Goals riding with the data (decision 16, R4).** Each area's `goals` hook does this, from the open goals' numbers and the query. Open goals only, as `goal_numbers` counts them. `lift_sets` and `lift_sessions`: lift goals, only those whose lift passes the `exercise`, `exercises`, `category`, `region` or `muscle` filter when one is sent (a muscle at either weight, R13). `cardio`: cardio_best goals, narrowed by `activity` or `category`. `weigh_ins`: body weight goals. `measurements`: measurement goals, narrowed by `kind`. `days`: every open goal. `food` and `saved_foods`: none. An empty list when none match. A goal's `plan_line` there reads, for example, "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind."

**Weeks, months and the range (decision 8).** `from_date` and `to_date` both taken; left out, the 28 days ending today. A week runs Monday to Sunday (`date.weekday() == 0` starts it); a month is the calendar month. A period that runs past either end of the range is a part period: its `days` cell says how many of its days are in range.

### The cursor (decision 11, R6)

`more` is `"c1." + base64url(compact JSON {"q": hash, "f": from, "t": to, "o": offset})`, padding stripped, about 90 characters. `hash` is the first 16 hex characters of SHA-256 over the canonical JSON of the query as sent, `cursor`, `limit`, `from_date` and `to_date` left out, every name in its base form (R3) and every filter name as matched. `f` and `t` are the range the first page used: a next page reads that range, so a page after midnight reads the same one. A `from_date` or `to_date` sent with a cursor must equal `f` or `t`, so a model that sends back the answer's `from` and `to` is taken; another date is `cursor_changed`. `o` counts the rows given before. A next page reads from `o`, at its own `limit`, under the same ceiling. A cursor that does not decode, or whose `c1.` prefix is missing, is `invalid_field`; one whose hash differs from the query sent is `cursor_changed`. No server state: nothing is kept between calls. An `o` past the end gives empty rows and no `more`.

### Row limits and the ceiling (R7)

`limit` 100 by default, at most 300. The ceiling: the answer's text as `Wrapper.call` sends it (`json.dumps` compact, `ensure_ascii=False`, `app/mcp_server.py:323-327`) is at most 30,000 characters. The service builds the answer with no rows, then adds rows in order while the text, counted with its final summary, `more` and note, stays at or under the ceiling. When the ceiling stops it before `limit`, `more` and the size note are set. No single row can pass it: a description is at most 200 characters and notes at most 2,000 (`app/rules.py:96`, `:100`).

Measured on 2026-11-16, on a scratch SQLite account seeded by a prototype of the 16-week seed below, with this contract's columns and compact JSON:

| Answer | Rows | Characters |
|---|---|---|
| `lift_sets`, default range, every field | 108 | 7,224 |
| `lift_sets`, 112 days | 100 | 6,771 (63.6 a row) |
| `lift_sets`, 112 days | 300 | 19,472 |
| `food`, 112 days, every field | 300 | 29,507 (97 a row) |
| `food`, 300 rows with 200-character descriptions | 300 | 142,844: the ceiling cuts it near 60 rows |
| `weigh_ins`, 364 days | 300 | 7,341 |
| `lift_sets` by month and exercise, 3 totals | 24 groups | 1,246 |
| `what_is_logged`, 8 areas, 76 fields with `muscle`, each field's totals as one text, 13 muscle names, `targets`, 2 goals, the final `how` | | 11,313 |

## The size of the list (the owner's addition of 2026-11-16)

Each new function's whole `tools/list` entry (name, title, description, annotations, input schema, output schema) is **at most 7,500 characters** as compact JSON, as `tests/test_feedback.py` writes it (`compact`). A test in `tests/test_data.py` holds it on the owner's list.

Measured on 2026-11-16 with a prototype: these exact descriptions and models, registered on a bare `MCPServer` of the same library, `additionalProperties: false` added at the input's top as `build_server` adds it. The same method gave `get_progress`' output schema as 17,173, the coordinator's figure.

| Entry | Description | Input schema | Output schema | Whole entry |
|---|---|---|---|---|
| `what_is_logged` | 355 | 96 | 4,016 | **4,669** |
| `get_data` | 490 | 2,204 | 4,110 | **6,994** |

Measured again on 2026-11-16 after the review's fixes, with everything in: nullable inputs (R2), `group_by`'s `min_length` and `max_length` (R11), `filters` and `totals` as `dict[str, Any]`, `targets` in both answers (R5), and the final descriptions, `get_data`'s with its units sentence (decision 13). The muscles add nothing to either entry: they are rows of the field list, read in `what_is_logged`'s answer. What keeps them small: the goals as `OpenGoal`, five fields with short descriptions (R4), not `GoalNumbers` whole; no per-area model, rows and fields as lists under a columns header. **The bar is 7,500**, kept: `get_data` is 506 under it, `what_is_logged` 2,831. Neither is over 7,500. A word added to an entry is measured first. The builder measures the real entries and gives both figures in the hand-over, for the build log entry written at the commit; an entry over 7,500 stops, and goes back to the owner.

## Model-read text (decision 18)

### The instructions

`DATA_RULE = "For any other question about the person's data, call what_is_logged, then get_data."` goes into `INSTRUCTIONS` right after `GOALS_RULE`, followed by a space, as `TRENDS_RULE` and `GOALS_RULE` are joined (`app/mcp_server.py:210`). It cannot go last: two tests hold that the instructions end with the plan_line sentence. **3,927 characters become 4,011**, under 4,096 (printed on 2026-11-16 by replacing the text in `INSTRUCTIONS` on this tree). `INSTRUCTIONS_AS_BUILT` (`tests/test_feedback.py:92`) takes the new hash.

### The two descriptions, in full

`what_is_logged`, title "What is logged", `READ_ONLY`, no arguments, 355 characters:

"What Spotter can hold and what this person has logged: each area get_data reads, with its fields, what each means, its unit, and the totals, filters and groups it takes, how many entries it holds and when, the names logged, and every open goal with its plan line. Call it before get_data, for a question the other reads do not answer. Quote these numbers."

`get_data`, title "Get data", `READ_ONLY`, 490 characters:

"One query over the person's own entries, for a question the other reads do not answer. Name an area, fields, filters, group_by and totals by the names what_is_logged gives. Spotter works out every total: quote them, never work out your own. Left out, the range is the last 28 days. Rows are lists under columns. Unlike the other functions, its numbers are in the person's units, and each column's name says which. When more is given, send it as cursor with the same query for the next rows."

No field meaning, area name or total rule is in either description: they are in `what_is_logged`'s answer.

### docs/can-and-cant.md

Two rows at the end of the function table, after `get_routines` (`docs/can-and-cant.md:456`), in this order:

| Function | What it does |
|---|---|
| `what_is_logged` | What Spotter can hold and what the person has logged, area by area, with the open goals. |
| `get_data` | One query over the person's entries: an area, its fields, filters, groups and totals, all worked out by Spotter. |

A new section, "Data on request", after "Routines" (`docs/can-and-cant.md:348`):

- "**Two reads.** `what_is_logged` names every area, its fields, what each means and the totals each takes, with how much the person has logged. `get_data` reads one area: rows, or groups with totals."
- "**Areas:** lift sets, lift sessions, cardio, weigh-ins, measurements, food, saved foods, and days, one row a day across them. Goals and routines stay with `get_goals` and `get_routines`."
- "**Totals** are Spotter's: sum, avg, max, min, count, per_day and share. per_day divides by the days with an entry in the area, or for a measurement by the days with that kind. A share is a whole percent, and the shares sum to 100. A pace over a range is total minutes over total distance."
- "**Ranges.** Left out, the last 28 days. Weeks run Monday to Sunday and months are calendar months."
- "**Size.** 100 rows by default, at most 300, and an answer of at most 30,000 characters. `more` gives the next rows."
- "**Units.** Every value is in the person's units, and each column's name says which."
- "**Body region** of a lift (upper, lower, core or full body) comes from its main muscles in free-exercise-db, save the two carries, which are full body."
- "**Muscles.** Each lift's muscles are free-exercise-db's. Grouped by muscle, a set counts 1 for its main muscle and 0.5 for each other muscle it works."
- "**Not offered:** joins across areas in one call, deleted entries, raw queries."

## Call log, read limit, annotations (decisions 18, 21)

- `NEEDS` gains `"what_is_logged": "read", "get_data": "read"`, after `get_routines`, so both join `READ_FUNCTIONS` and count toward the 300 reads together (`app/mcp_server.py:257`, `app/services/common.py:413-424`).
- `KNOWN_NAMES` gains `area`, `fields`, `filters`, `from_date`, `to_date`, `group_by`, `totals`, `order`, `cursor`; `limit` is there. So `tests/test_trace.py:233` holds. The keys inside `filters` and `totals` are field names, not argument names: `tried` writes the known ones in braces and counts the rest as `other[n]` (`app/calllog.py:204-205`), never a value.
- Both use `READ_ONLY` (`app/mcp_server.py:229`): `readOnlyHint` true, `destructiveHint` false, `idempotentHint` true, `openWorldHint` false.
- Registered after `get_routines`, before the owner's three: everyone's list is the 22, then `what_is_logged`, `get_data`. 24 for everyone, 27 for the owner. `web.FUNCTION_COUNT` follows by itself (`app/web.py:1234`).

## Performance (decision 19)

- **The bar,** timed by the builder on this PC on SQLite, the service function called directly, median of 5 calls, on an account holding a year of daily entries in every area (365 sessions of 9 sets, 3,650 food entries, 365 weigh-ins, 365 cardio entries, 730 measurements, 10 saved foods): `get_data` over the whole year at the default limit, for each area, at most 500 ms; with `group_by` week and two totals, at most 500 ms; `what_is_logged`, at most 1,000 ms. The figures go in the builder's hand-over, for the build log entry written at the commit. Not measured here.
- **No index** unless a bar is missed. An index is a migration (`0012`), owes a Postgres run of up, down, up, and goes back to the owner before it is built.
- **Statements:** a test counts SQLAlchemy statements (`before_cursor_execute`) for one `get_data` call per area and one `what_is_logged`, at 10 rows and at 1,000: the counts are equal.

## Packages: two builders, D1 then D2

D2 reads D1's functions, and both touch `tests/test_inputs.py`, `tests/test_hi_there.py`, `tests/test_routines.py` and `tests/test_units.py` (D1 for the instructions pin, D2 for the case order), so they build one after the other in the same tree.

### D1: the field list, the two functions, the surface (high)

| Files | Exit check |
|---|---|
| `app/services/fields.py` (new), `app/services/data.py` (new: `what_is_logged(user_id)`, `get_data(user_id, data)`), `app/services/__init__.py` (both exported), `app/exercises.py` (`REGIONS`, `MUSCLES`), `app/formatting.py` (`shown_number`, `minutes`, `volume`), `app/mcp_schemas.py` (`OpenGoal`, `AreaLogged`, `WhatIsLoggedResult`, `RangeTotals`, `GetDataResult`, two `RESULTS` entries, module notes), `app/mcp_server.py` (`DATA_RULE`, `INSTRUCTIONS`, `NEEDS`, the two functions, module notes), `app/calllog.py` (`KNOWN_NAMES`), `docs/can-and-cant.md`, `tests/test_data.py` (new). Existing test files only where a decision moves an assertion, each named in the hand-over: `tests/test_mcp.py`, `tests/test_feedback.py`, `tests/test_switch.py`, `tests/test_edit_history.py`, `tests/test_hi_there.py`, `tests/test_idempotency.py`, `tests/test_inputs.py`, `tests/test_routines.py`, `tests/test_units.py`, `tests/test_formatting.py` (`:37`, `:149` and `test_step`: `minutes` and `volume`) | Every ruling and every row of the refusal table has a test, each message and fix word for word. The tie test and the scaling test pass. Both entries are at most 7,500 characters. The instructions are 4,011 characters and end with the plan_line sentence. Of the published list, the 22 are byte for byte as before. The tests for the files touched pass, plain and with `-W error` |

### D2: the seed and the eval cases (medium)

| Files | Exit check |
|---|---|
| `evals/sixteen_weeks.py` (new), `evals/judge.py` (`NO_DATA_SERVICES` and `seed_service` by name, the notes), `evals/cases.json` (8 cases, last, in the order below), `docs/evals.md` (the seed, eight rows in the case table, a short section as the units cases have, and at `:68` ", twenty-four since what_is_logged and get_data" after "twenty-two since `save_routine` and `get_routines`"), `tests/test_sixteen_weeks.py` (new), `tests/test_evals.py` (`CONTRACT_CASES`, `NO_DATA_SERVICES`), and the order pins: `tests/test_inputs.py:1220`, `tests/test_hi_there.py:1330-1331`, `tests/test_routines.py:1278-1279`, `tests/test_units.py:845` | The seed's figures hold on nine fixed todays (below). The cases load, and each judges a passing and a failing run. `python -m evals.need main` says yes. The tests for the files touched pass, plain and with `-W error` |

### Pins that move

| Pin | Now | After |
|---|---|---|
| `FUNCTIONS` | `tests/test_mcp.py:253-257` | gains `what_is_logged`, `get_data` at the end |
| `READS` | `tests/test_mcp.py:258-259` | gains both |
| `every_result` | `tests/test_mcp.py:317` | calls both, see the tests |
| `len(INSTRUCTIONS) == 3927` | `tests/test_switch.py:567`, `tests/test_edit_history.py:661`, `tests/test_hi_there.py:1232`, `tests/test_idempotency.py:735`, `tests/test_inputs.py:131`, `tests/test_routines.py:854`, `tests/test_units.py:705` | 4011 |
| `INSTRUCTIONS_AS_BUILT` | `tests/test_feedback.py:92` | the new hash |
| `PUBLISHED` | `tests/test_feedback.py:68-91` | unchanged: no existing entry moves |
| Owner's list order | `tests/test_feedback.py:516-531`, `tests/test_routines.py:841` | the two names after `get_routines` |
| `BEFORE_INPUTS` and the sweep's base calls | `tests/test_inputs.py:40`, `:114`, `:248` | the old names compared as before; the sweep gains a valid base call of each new function, so its bad values reach `get_data`'s fields |
| Eval case count and order | `tests/test_inputs.py:1220` (`len(cases) == 43`), `tests/test_hi_there.py:1330-1331`, `tests/test_routines.py:1278-1279`, `tests/test_units.py:845`, `tests/test_evals.py:25`, `:50-51` | 51, each pin moved by 8 (D2) |
| `NO_DATA_SERVICES == {"four_weeks"}` | `tests/test_evals.py:1064` | `{"four_weeks", "sixteen_weeks"}` (D2) |

## The tests

### D1: `tests/test_data.py`

Every date is worked from a fixed today set through `db.utcnow` (`app/models.py:70-71`), never the real date.

- **The field list.** The tie test and the scaling test above. Every area has a count field; every field's totals are a subset of the seven; every `columns` name exists; `REGIONS` and `MUSCLES` cover the 41 lifts exactly, each region by the rule from its muscle save the two exceptions.
- **Each area's rows** on a seeded account: the cells of one known row of each area, every derived field among them (estimated max null at 12 reps and at 0 kg, volume 0 at 0 kg, pace null without a distance, food's `food` for a saved, a USDA and a plain entry, the plain one grouping "Chicken  Biryani" with "chicken biryani").
- **Deleted entries left out:** a deleted set's session, a replaced weigh-in, a deleted food entry and saved food appear in no row, total, count, `filled` or `entries`.
- **Never logged:** an area with nothing gives empty rows and "Nothing is logged in {area} yet."; a range before any entry gives the last entry's date; a field never filled is null in every row, its totals null and count 0, and `filled` 0.
- **Filters,** each kind, a list of names, and every refusal of the table, word for word, several in one call joined by `rules.combine`.
- **Totals,** each on a hand-worked set: per_day over the days with an entry with a meal filter (the divisor unchanged); share by largest remainder summing to 100 over three groups that round to 99 alone, with the tie rule; a page of groups whose shares do not sum to 100; pace avg as total minutes over total distance against the average of paces; `value` totals with one kind, two kinds refused, a sum of waist refused.
- **Groups:** by muscle, a bench set adds 1 to chest and 0.5 each to shoulders and triceps in `sets_sum`, `count` is 1 in each, the weighted volume is half the set's for a secondary, the shares sum to 100, the range `totals` count the set once, and Suitcase Carry's sets sit under a null muscle; a muscle filter keeps a set whose lift works it as a secondary, and with `group_by` muscle shows only the muscles named; a muscle filter narrows the riding goals; `muscle` is not a default column, and named in `fields` reads the primary muscle; `what_is_logged`'s `names` for muscle are most weighted sets first; by category, push against pull; a week group over a range that starts on a Wednesday gives its `days` 5; an empty week has count 0; a month edge; two groups; the default and a sent order.
- **Units:** on a lb account every weight column is `_lb` and 97.5 kg reads 214.95; on a mi account 5 km reads 3.11 `distance_mi` and pace per mi; `weight_kg` and `weight_lb` both taken in `fields`, `totals` and `order`; a cell never a float that is whole.
- **The cursor:** a 250-row query at limit 100 pages to the end in three calls with no row twice or missed; the same cursor with a changed filter is `cursor_changed`; a broken one `invalid_field`; a page after the fake clock passes midnight reads the first page's range.
- **The ceiling:** 300 food entries with 200-character descriptions: the answer is at most 30,000 characters, carries `more` and the size note, and paging to the end gives every row once.
- **Goals:** a squat filter carries the squat goal and not the bench goal; food carries no goal; a closed goal never rides; each goal's five values equal `get_goals`' for the same goal. Both answers' `targets` equal `whoami`'s on an account with all three set and on one with none; no other setting is in either answer, at any depth.
- **per_day by rows:** steps logged on 10 of 20 days and a waist on the other 10: steps' per_day divides by 10, and `days_logged` is 20.
- **The scaling hooks:** R9's source test, exactly as R9 words it: whole-string equality, the exempt set `{"entries"}` and no other.
- **One person never sees another's rows:** two accounts with entries in every area, goals and saved foods each. Each account's `what_is_logged`, and its `get_data` on every area, with no group, with a time group and with a field group, holds none of the other's rows, ids, counts, `filled`, `names` or goals; every count equals its own seed's.
- **The review's rulings:** `fields: []`, a blank `area`, a filter value 5 and a totals value "sum" each get the table's words; `totals: {}` is taken as left out; `value`'s sum with `group_by` kind and no kind filter is refused; a default `from_date` near `log.oldest_day` is the floor, never refused; the `exercises` filter keeps whole sessions; saved_foods' default order; the column names of a time key, a field key and the period cells; two groups' default order; a pace ordered by its minutes.
- **Three groups** get the schema refusal's words, and nothing is read.
- **`what_is_logged`** on the empty account (note, every count 0, `first` null) and on a full one: its counts, `names` most used first, goals equal to `get_goals`' for the same goals.
- **Through `/mcp`:** both published with these descriptions, these arguments in this order, `READ_ONLY`, every input and result field described; each entry at most 7,500 characters; a null input taken as left out, the same answer as the call without it; both count toward the 300 reads; the call-log row's `tried` for a get_data call reads `area filters{exercise} group_by[1] totals{kcal}`-like names and never a value.
- **Statements:** the count test of "Performance".
- **Text:** every message, fix, summary, note, line, meaning and `how` through `tests/text_rules.py` on its surface.

`tests/test_mcp.py`'s `every_result` gains, in place: `what_is_logged` on the first person before anything is logged (its `note`), and `get_data` area food there (empty rows, `note`, `range_note`); on the four-week person, `what_is_logged` (names, goals), `get_data` lift_sets with an exercise filter and totals (`goals`, `totals`), and food with `limit` 1 (`more`). `targets` is in every answer of both. So every top-level field of both models appears in a real result.

### D2: `tests/test_sixteen_weeks.py`

Seeds a fresh account at nine fixed todays (Monday 2026-11-16 to Sunday 2026-11-22, then 2026-12-01 and 2027-03-01) and asserts, through `services.get_data` and `services.get_goals`, every figure the eight cases look for, as the next section lists them. Also: the counts it returns; every row carries its kind's duplicate key; `not_empty` on an account that has entries.

## The eval cases (decision 20)

### The seed: `sixteen_weeks` (`evals/sixteen_weeks.py`)

`sixteen_weeks(user_id)`, called as `four_weeks` is, into an empty account (`account.account_empty`), straight through the models in one transaction, every row `logged_by` "web", each with its kind's duplicate key (`lifts.session_key`, `body.weigh_in_key`, `measurements.measurement_key`, `food.entry_key` per meal, `cardio.cardio_key`), settings never touched. No randomness. `n` is days before today; the 112 days are `n` = 111 down to 0.

| Part | What it logs |
|---|---|
| Weigh-ins | One a day for 364 days, `n` = 363 to 0. For `n` of 21 or more, 80 kg plus 0.02 kg for each day past 21; below 21, 80 kg plus 0.05 kg for each day short of 21; both worked in Decimal and kept at 1 dp, halves up. Except three: `n` 330 is 91.2, 340 is 92.4, 350 is 90.6. Every other weigh-in is at most 86.8 |
| Lift sessions | Monday, Wednesday and Friday of the 112 days: 48, A and B in turn from A, oldest first. A, its `i`-th time from 0: Back Squat 3 × 5 at min(70 + 2.5 i, 100) kg, Bench Press 3 × 5 at 40 + 2.5 i kg, Barbell Row 3 × 8 at 60 kg. B, its `j`-th time: Deadlift 3 × 5 at 100 + 2.5 j kg, Overhead Press 3 × 5 at 30 + 1.25 j kg, Pull-Up 3 × 6 at 0 kg. Every session is 3 lower sets and 6 upper |
| Cardio | For `n` of 21 or more only: Tuesday Outdoor Run, 30 minutes, 5 km; Thursday Stationary Bike, 30 minutes. Estimates from body weight now (`metrics.bodyweight_now`), as `four_weeks` does |
| Measurements | Waist on today's weekday, 16 weeks: 92 cm for the oldest, then 0.25 cm less each week. No other kind |
| Saved foods | Oats bowl (1 bowl, 300 kcal), Roti (1 roti, 120), Dal bowl (1 bowl, 180), Rice bowl (1 bowl, 200) |
| Food, `n` of 21 or more | Breakfast: Oats bowl × 1, "Tea with milk" 80 kcal you_said. Lunch: Roti × 2, Dal bowl × 1, "Mixed vegetable sabzi" 150 kcal claude_estimate. Dinner: Rice bowl × 1, Dal bowl × 1, "Chicken curry" 350 kcal claude_estimate. 1,680 kcal a day |
| Food, `n` below 21 | Breakfast and lunch the same. Dinner: "Chicken biryani" 640 kcal you_said, Dal bowl × 1. Snack: "Coke 500 ml" 210 kcal you_said. 1,980 kcal a day |
| Goals | Body weight 78 kg: start 82 kg on today less 112 days, target today plus 56, down. Back Squat 150 kg: start 80 kg on today less 112 days, target today plus 84, up. Both open |

The figures, worked on 2026-11-16 by running a prototype of this seed on a scratch SQLite account through Spotter's own `get_goals` and `get_trends`, and the same on the other eight todays: every figure below held on all nine.

| Figure | Value |
|---|---|
| Sets by region, any range | lower 33.33%, upper 66.67%, core and full_body none: shares 33 and 67 |
| Back Squat | 100 kg × 5 since its 13th A session; estimated max 116.7 kg in every range of the last 7 weeks |
| Squat goal's plan_line | "116.7 kg now against 120 kg on the plan line today: 3.3 kg behind." |
| Body weight goal's plan_line | "80.9 kg now against 79.3 kg on the plan line today: 1.6 kg behind." |
| Food, last 28 days | 28 days logged, 53,340 kcal, 1,905 a day. Per day by meal: dinner 797.5 (798), lunch 570, breakfast 380, snack 157.5 (158). Shares 42, 30, 20, 8 |
| Food, last 28 days, by food | Chicken biryani 21 times, 640 each, the largest sum; Dal bowl 56 times; Coke 500 ml 21 times, 210 each |
| Bench Press, last 28 days, on a lb account | heaviest 97.5 kg, shown 214.95 lb |
| Weigh-ins above 90 kg in 364 days | 91.2, 92.4 and 90.6, at `n` 330, 340 and 350 |

`judge.NO_DATA_SERVICES` becomes `{"four_weeks", "sixteen_weeks"}`, and `seed_service` returns the function of the name asked (`evals/judge.py:212-222` returns `four_weeks` for any name in the set today). `evals/run.py:231` needs no change.

### The eight cases

`W` below is the no-write list `how_am_i_doing` uses: `["log_lifts", "log_bodyweight", "log_measurement", "log_food", "save_food", "log_cardio", "set_goal", "update_settings", "edit_entry", "restore_entry", "delete_entry", "import_entries", "send_feedback", "save_routine"]`, written out in full in the file. No figure in a `reply_has` carries a thousands comma.

```json
{"id": "data_basketball",
 "say": "How do I get better at basketball?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"function": "get_data", "inputs_include": {"area": "lift_sets"}, "no_call": W,
            "any_of": [{"reply_has": ["33%"]}, {"reply_has": ["67%"]}, {"reply_has": ["a third"]},
                       {"reply_has": ["two thirds"]}]}}

{"id": "data_lifting",
 "say": "How do I get better at lifting?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"function": "get_data", "inputs_include": {"area": "lift_sets"}, "no_call": W,
            "any_of": [{"reply_has": ["116.7"]}, {"reply_has": ["100 kg"]}]}}

{"id": "data_calories",
 "say": "How do I cut down on my calories? Where do I remove it from day to day?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"function": "get_data", "inputs_include": {"area": "food"}, "no_call": W, "reply_has": ["biryani"],
            "any_of": [{"reply_has": ["640"]}, {"reply_has": ["798"]}, {"reply_has": ["42%"]}]}}

{"id": "data_squat_goal",
 "say": "How are my squats going?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"no_call": W, "reply_has": ["3.3"],
            "any_of": [{"function": "get_data",
                        "inputs_include": {"area": "lift_sets",
                                           "filters": {"exercise": {"$in": ["Back Squat", ["Back Squat"]]}}}},
                       {"function": "get_progress", "inputs_include": {"exercise": "Back Squat"}}]}}

{"id": "data_back_on_track",
 "say": "I have slipped lately. How do I get back on track?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"no_call": W, "reply_has": ["1.6"],
            "any_of": [{"function": "get_goals"}, {"function": "what_is_logged"}, {"function": "get_data"}]}}

{"id": "data_lb",
 "say": "What is the heaviest I have benched in the last 4 weeks?",
 "seed": [{"service": "update_settings", "data": {"weight_unit": "lb"}}, {"service": "sixteen_weeks"}],
 "expect": {"no_call": W, "reply_has": ["214.95"], "reply_lacks_pattern": ["\\bkg\\b"],
            "any_of": [{"function": "get_data", "inputs_include": {"area": "lift_sets"}},
                       {"function": "get_progress"}, {"function": "get_log"}]}}

{"id": "data_empty",
 "say": "How has my sleep been lately?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"no_call": W, "reply_lacks_pattern": ["\\b\\d+(\\.\\d+)?\\s*(hours|hrs|h)\\b"],
            "any_of": [{"function": "what_is_logged"}, {"function": "get_data"}]},
 "db": [{"table": "measurement", "count": 16, "all": {"kind": "waist"}}]}

{"id": "data_year_more",
 "say": "Was my weight ever above 90 kg in the past year? Which days, and what did I weigh?",
 "seed": [{"service": "sixteen_weeks"}],
 "expect": {"function": "get_data", "inputs_include": {"area": "weigh_ins"}, "no_call": W,
            "reply_has": ["91.2", "92.4", "90.6"]}}
```

| Case | Proves | Does not prove |
|---|---|---|
| `data_basketball` | A sport Spotter cannot log still gets Spotter's own split of the person's training, quoted | That the advice is good, or that core work being none is said |
| `data_lifting` | A lifting question reads the sets and quotes the squat's stall | The bench's rise, or the goal's plan line |
| `data_calories` | A calorie question reads food by meal or by food, and names the biggest item with its figure | That every meal's share is quoted right |
| `data_squat_goal` | The squat goal rides with the lift's data, and its plan line is said | Which of the two reads was chosen: either is right |
| `data_back_on_track` | "Back on track" reaches the body weight plan line. "on track" still routes to `get_goals`, and the three existing cases stay as they are | That the slip in calories is named |
| `data_lb` | A lb account hears 214.95, never kg, and nothing converts | Any other unit or field |
| `data_empty` | A never-logged kind gets no invented figure, and nothing is written | That the reply says sleep can be logged |
| `data_year_more` | A year's question finds the three days, by paging with `more` or by ordering or `max` | That `more` itself was sent: the judge has no "key present" form, so the case judges the figures. Ordering by `-weight_kg` or a `max` by month passes too, and is right |

**The run.** Not per item: the release's one run, 51 cases at 3 runs on `claude-opus-5-5`, about 51 × $0.49 = about $25, on the Claude side. Nothing on Railway. The data cases read a `what_is_logged` answer of about 11,600 characters, so they may cost more a case than the last run's average: not measured. The cost is told to the owner before it runs.

## What does not change

- Today's 22 functions: their names, descriptions, inputs and results, byte for byte (`PUBLISHED`), and their order. `get_trends` still answers "how am I doing" and `get_goals` "on track".
- Stored units: kg and km. No row is written by either function.
- No migration, no table, no column. The one Postgres run is a test run (exit check 6), not a migration's.
- `list_exercises` and the exercises resource: no region in them.
- The read limit's number, the write limit, the switch: a read is open to every account.
- The pages: none shows either function's answer.
- **The words goal and target follow today's published functions:** `set_goal`, `get_goals` and `GoalNumbers` for finish lines by a date, `whoami`'s `targets` and `update_settings` for the daily and weekly amounts. Both new answers reuse `targets` as `whoami` publishes it, and add no other name for either. Any rename is a later item, and a renamed result field takes two deploys.

## The exit check

1. Every row of both packages' exit checks holds.
2. Both published entries at most 7,500 characters, measured on the built server, and the figures in the build log entry written at the commit; the performance figures there too, under their bars.
3. The reviewer, on a frozen copy, calls both functions through `/mcp` on a seeded account: every refusal of the table, a page to the end with the cursor, the ceiling with long descriptions, a lb account, and the scaling test's throwaway area by probe.
4. The tests for the files touched pass, plain and with `-W error`. The hook runs the rest.
5. `python -m evals.need main` says yes: the release's one run is owed, its cost said first.
6. One local Postgres run of `tests/test_data.py` before the merge, on the owner's okay to start Docker, recorded in the build log entry.

## Notes after the build

**2026-11-16, D1 built and reviewed on a frozen copy, one fix round, a re-check.** Where the build settled a point the text above left open or worded two ways, the build is the record:

| Point | As built |
|---|---|
| R9's exempt set | `{"entries", "values"}`, as R9 says. The tests line that names `entries` alone is superseded |
| per_day on lift_sessions' `sessions` | None: its field row gives share only. Sessions over the days with a session is always about 1 |
| Nothing in range, with a time group | Empty rows and the note (decision 12). When anything matches, every period of the range is listed |
| Words the refusal table did not give | A to_date before the floor, a from_date after today, a filter or totals value of the wrong type, a page past the end: each has its own refusal or summary, through `tests/text_rules.py` |
| Two more `Area` hooks | `goal_kinds` (from `goals.KINDS_NOW`) and `entries_are_days`: data on the entry, as R9 asks |
| A forged cursor | A cursor longer than 200 characters is refused before it is read, and a range outside the account's floor or after today is refused: "cursor is not one Spotter gave." Spotter's own are about 90 |
| `value`'s range totals over several kinds | Null: a range total never mixes units. One kind keeps its figure |
| `value`'s share | Needs a one-kind filter, `group_by` kind included: "value's share needs one kind: it is a part of one kind's total." |
| The goals that ride along | At most 10, in `get_goals`' order, with the note "{n} more open goals: get_goals has them all." A page always keeps at least one row, so `more` moves on |
| The saved food join | Also on the entry's own account, as a second guard |
| The order refusal's example | "such as -{the answer's last column}", since -kcal_sum is not a column of most answers |
| Statements with an open lift goal | They grow by at most 2 for each 500 sessions, on the path `get_goals` already takes (`lifts.history`'s two `selectinload` levels), never one a row. A known limit |

**2026-11-17, D2 built and reviewed on a frozen copy, one fix round, a re-check.** The build is the record where it differs from the text above:

| Point | As built |
|---|---|
| `data_year_more` | A max by week finds all three high weigh-ins; a max by month does not, since all three fall in one month on some todays. The case table's "a max by month passes too" is superseded |
| A new judge key | `reply_has_pattern`: regular expressions the reply must match, each compiled when the cases load. It keeps "3.3" from matching inside 93.3 |
| `data_squat_goal`, `data_back_on_track` | Each passes on either a whole-number match of the plan line's gap (3.3, 1.6) or its plan figure ("120 kg on the plan line", 79.3), with its routes inside each option, since the judge takes one `any_of` an expect |
| `data_empty` | Two narrower patterns: a made-up sleep figure for the person fails, general advice such as "most adults aim for 7 to 9 hours" passes |
| `data_lifting` | Also needs "squat", so the deadlift's 100 kg alone does not pass |
| `data_basketball` | "a third of" and "two thirds of", so "add a third day" does not pass |
| Cardio and food in the seed | Within the 16 weeks; weigh-ins run the full year |

**2026-11-17, the release run and the eval fix round.** The release's one run, 51 cases at 3 runs with `--model claude-opus-5-5`, gave 144 of 153. Its model line names `claude-fable-5-2` too: 11 runs list it beside `claude-opus-5-5`, 10 of them first, the one that cost the most. In every answered run the reply quoted Spotter's figures right. The owner's target for this item: "what we need to do is have a comprehensive desc that allows the model to build a data req cleanly to answer generic enough questions. that was the target here." So the cases now judge that a data request was built and that Spotter's figures were quoted, not one route or one split. The build is the record where it differs from the text above:

| Point | As built |
|---|---|
| `data_basketball` | 0 of 3: two of the runs read only `what_is_logged` and quoted its figures. The owner chose to accept `what_is_logged`: the case now passes on a read by `what_is_logged` or by `get_data` on any area, carried in each figure option as `data_lifting` does. `get_data` is no longer required. Whether the model builds a `get_data` request well is now the job of the function list study. `inputs_include` lift_sets and the 33%, 67%, "a third of", "two thirds of" options are gone. The reply must quote one figure Spotter gives from the seed, as a figure of its own (`reply_has_pattern`, digit guards): 12 or 48 lift sessions, 108 or 432 sets, 2 or 26 cardio entries or 8 in the 28 days before, 33% or 67% beside upper or lower, the squat's 116.7 or 100 kg x 5, or a plan line's 3.3 or 1.6 kg behind, 120 kg on the plan line, 79.3 or 80.9. A small count needs its noun or its window. The counts hold on all nine todays of `tests/test_sixteen_weeks.py`, through `what_is_logged`. The case table's "Spotter's own split ... quoted" is superseded |
| `data_lifting` | 1 of 3: the two others read the lifts by `get_progress`, with the right figures. Now `get_data` on lift_sets or `get_progress`, in each figure option, as `data_squat_goal` does. No judge change |
| `data_empty` | 2 of 3: run 2's `for example "7.5 hours last night"`, an example of what to say, matched the made-up figure patterns. Both patterns now pass a figure whose sentence has "for example", "for instance", "e.g." or "such as" before it, one directly after "like", and one after a double quote in its sentence. "You slept 7 hours" and "about 6.5 hours a night" still fail |
| `data_calories` run 3, `data_back_on_track` run 2 | Cut off at 180 s after 10 to 15 right calls, made one a turn. The server answered `get_data` in 32 ms on average. `evals/run.py`'s limit is now 300 s, in `docs/evals.md` too |
| A sentence in `get_data`'s description that independent queries can go in one turn | Not built. It moved to a later study of the whole function list. `app/` is unchanged by this round |
| Judged again on the kept runs | `data_lifting` 3 of 3, `data_empty` 3 of 3, `data_basketball` 3 of 3. `feedback_offered` run 2, an older case, is left as it is |

## Known limits

| Limit | Why it stands |
|---|---|
| A row logged or deleted between two pages can move one row across the page edge | The cursor is an offset (R6) |
| No join across areas in one call: food against weight takes two calls, or the `days` area | Decision 1 |
| With two groups, a share is of the range total, not of its week | Decision 10's words. One group with a one-week range gives the split within a week |
| Muscle weights are 1 and 0.5, not a measured share per lift; free-exercise-db's 17 names are the finest split | R13: the weights with research behind them; a finer source is later |
| `data_year_more` passes without `more` being sent | The judge has no "key present" form |
| The daily and weekly amounts are today's only: a past week is not read against the amount that held then | The settings keep one value with no history. Amounts as rows with their dates are a later item |
| A conversation begun before the deploy holds the old list: no `what_is_logged`, no `get_data`, and the old instructions | As every function change. A new conversation sees them |

## Not in this work

The card's "Scoped, not in this build": one field list for writes, targets as rows, a goal as a change, new sports and their parts, a set's time or distance. Basketball as an activity. Streaks. A page for either function, and the body map. Lifts beyond the 41.

## Note, 2026-11-20: the exercise list (`docs/contracts/exercise-list.md`)

The volume rule, by load type and sides (`volume_kg` on `lift_sets`, `lift_sessions` and `days`): weight x reps, x 2 for a pair, x 2 for one side at a time; 0 at 0 kg or below, so a band set and a set with help count 0; a set of time or distance has none, and a session or day of holds or carries alone counts 0.

The 41-lift table above stays the record of 2026-11-16. The list in code (`app/exercise_rows.py`) is now the home of every lift's muscles, primary at 1 and secondary at 0.5, and R13 (h) no longer binds: Suitcase Carry has muscles. Every carry and sled is full body.

`lift_sets` gains `time_s` and `distance_m` (default columns) and `family` (a column, a filter and a group). A plain word on `exercise` or `family` takes the lifts of it the person logged.

## Note, 2026-11-22: log and guide (`docs/contracts/log-and-guide.md`)

A saved food's `id` in `what_is_logged` reads "The saved food's id, to log it by.", naming no function, as `log_food` leaves every list for `log`. The table above keeps its words of 2026-11-16.

## Note, 2026-11-22: C3 (macros-and-asking.md)

The settings, `targets` in `what_is_logged` and in each `get_data` answer, gain the three daily goals in grams beside `calorie_target_kcal`: `protein_target_g`, `carbs_target_g` and `fat_target_g`, each null when not set (that contract's decision 2 A).

The `food` area gains `protein_g`, `carbs_g` and `fat_g` (grams, null when not logged, never 0 g; their totals skip the nulls), `macros_estimated` (true when the grams are an estimate, null with none) and `portion` (how much was eaten, in the person's words, or null). The `saved_foods` area gains `protein_g_per_serving`, `carbs_g_per_serving` and `fat_g_per_serving`. So a week's protein is Spotter's sum (its decision 17); `get_trends` carries no macro yet.

Measured on the built server, compact JSON characters of the published entry: `get_data` 7,495 of its 7,500 cap, `what_is_logged` 5,131 (`tests/test_data.py`, `test_each_new_entry_is_at_most_7500_characters`). `Targets` keeps its new words short for that cap.
