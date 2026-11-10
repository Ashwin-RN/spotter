"""The shapes of the sixteen MCP functions' arguments and results.

Each result model is published as that function's result schema, and the MCP library checks every
successful result against it. The top-level fields match the service's keys exactly, and a top-level
model forbids anything else, so a field a service adds without a schema entry fails a test instead
of passing silently. Fields that appear only sometimes default to None and are left out of the result
when absent. Every field has a description: that text is what the calling model reads.

An error result is not checked against these models. It is {error, message, fix}, sometimes with
`errors` (every problem at once) or `closest` (the nearest exercise names).
"""

from pydantic import BaseModel, ConfigDict, Field

TOP = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- arguments

class SetIn(BaseModel):
    """One set of a lift session."""

    exercise: str = Field(description="The exercise name, exactly as list_exercises gives it.")
    weight_kg: float = Field(description="The weight lifted in kg, 0 to 500. Use 0 for bodyweight work.")
    reps: int = Field(description="Reps done in this set, a whole number from 1 to 30.")


class FoodEntryIn(BaseModel):
    """One food in a log_food call. There is no tag field: Spotter works out who supplied the number."""

    description: str = Field(description="What was eaten, in the person's words, such as 2 rotis. 1 to 200 characters.")
    meal: str | None = Field(
        default=None,
        description="breakfast, lunch, dinner or snack, only when the person named it. Leave out otherwise: "
                    "Spotter picks it from their clock.")
    kcal: float | None = Field(
        default=None,
        description="The calories, 0 to 5,000: the person's number, or yours with estimated true. Never with "
                    "saved_food_id or usda_fdc_id.")
    estimated: bool | None = Field(
        default=None,
        description="true when you worked the kcal out yourself, always. Leave out or false when the person "
                    "stated it.")
    saved_food_id: int | None = Field(
        default=None, description="A saved food's id from find_food. Send servings with it, and no kcal.")
    servings: float | None = Field(default=None, description="How many servings of the saved food, 0.1 to 50.")
    usda_fdc_id: int | None = Field(
        default=None, description="A USDA food's fdc_id from find_food. Send grams with it, and no kcal.")
    grams: float | None = Field(default=None, description="How many grams of the USDA food, 1 to 5,000.")


# ---------------------------------------------------------------- shared pieces

class Projection(BaseModel):
    slope_per_week: float = Field(description="The trend: the fitted change per week over the last 6 weeks, in kg.")
    projected: float | None = Field(description="Where the trend line reaches on the target date, in kg.")
    status: str | None = Field(
        description="reached (already at or past the target), on track (the line meets it by the date) or behind.")
    points_used: int = Field(description="How many points the trend line used.")
    latest: float = Field(description="Where the person is now, in kg: the same value as the goal's latest.")
    weeks_left: float | None = Field(description="Weeks from today to the target date; 0 once it has passed.")
    needed_per_week: float | None = Field(
        description="The steady change per week that reaches the target by its date, in kg. Signed.")


class GoalNumbers(BaseModel):
    """Where one open goal stands. The same shape in set_goal and get_progress."""

    goal_id: int = Field(description="The goal's id. set_goal takes it as goal_id to change or close the goal.")
    label: str = Field(description="The goal in a few words, such as 'Back Squat 120 kg'.")
    kind: str = Field(description="lift or bodyweight.")
    exercise: str | None = Field(description="The lift, for a lift goal; null for a body weight goal.")
    unit: str = Field(description="The unit of every value here: kg.")
    start_value: float = Field(
        description="Where the goal started. A lift: its best session estimated max in the 28 days before the "
                    "goal was set. Body weight: the 7-day average, or the latest weigh-in when the 7 days had none.")
    start_date: str = Field(description="The date the goal was set, YYYY-MM-DD.")
    direction: str = Field(description="up or down: which way the goal points. It never changes.")
    target_value: float = Field(description="The target in kg.")
    target_date: str = Field(description="The date to reach it by, YYYY-MM-DD.")
    latest: float | None = Field(
        description="Where the person is now, measured the same way as start_value. Null with nothing logged.")
    status: str = Field(
        description="reached (latest is at or past the target), on track or behind (from the trend line), or "
                    "not enough data (too few points for a line, and not reached).")
    needed_per_week: float | None = Field(
        description="The steady change per week from latest that reaches the target by its date, in kg. Signed. "
                    "Worked out with or without a trend line; 0 once reached; null once the date has passed.")
    actual_per_week: float | None = Field(description="The trend so far, in kg per week. Signed.")
    projection: Projection | None = Field(description="The trend line's numbers, or null without enough points.")
    note: str | None = Field(description="Why there is no projection; null when there is one or the goal is reached.")


# ---------------------------------------------------------------- whoami

class Targets(BaseModel):
    calorie_target_kcal: int | None = Field(description="Daily calorie target, or null when not set.")
    target_sessions_per_week: int | None = Field(description="Training sessions a week, or null when not set.")
    target_cardio_minutes_per_week: int | None = Field(description="Cardio minutes a week, or null when not set.")


class WhoamiLinks(BaseModel):
    settings: str = Field(description="The Settings page: the person's account details.")
    home: str = Field(description="Spotter's home page.")


class SignIn(BaseModel):
    client_id: str = Field(description="The app the person signed in through.")
    scopes: list[str] = Field(description="The scopes the sign-in carries.")
    issuer: str = Field(description="Who signed the sign-in.")
    expires_at: int = Field(description="When this sign-in expires, in seconds since 1970 (UTC).")


class WhoamiResult(BaseModel):
    model_config = TOP

    account_id: int = Field(description="Spotter's id for this account. The Settings page shows the same number.")
    user_id: str = Field(description="The sign-in provider's id for the person. The Settings page shows the same.")
    email: str | None = Field(description="Their email address, or null when the provider has not said.")
    name: str | None = Field(description="Their name, or null when the provider has not said.")
    time_zone: str | None = Field(description="Their time zone (an IANA name such as Asia/Kolkata), or null.")
    today: str = Field(description="Today's date for the person, YYYY-MM-DD. Dates you leave out mean this day.")
    targets: Targets = Field(description="The person's targets. Not used by any function yet.")
    claude_can: str = Field(
        description="What the person allows Claude to do. read: read only. log (the default): read and every "
                    "write except delete_entry. log_delete: everything, delete_entry too. Only the person "
                    "can change it; Claude cannot.")
    links: WhoamiLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    sign_in: SignIn = Field(description="The sign-in this call came with.")
    time_zone_note: str | None = Field(
        default=None,
        description="Present only when no time zone is set: today is then the UTC date. Set it with "
                    "update_settings before logging anything: use the person's zone if you know it, otherwise ask "
                    "once which city they are in.")


# ---------------------------------------------------------------- list_exercises

class ExerciseItem(BaseModel):
    name: str = Field(description="The name to use in every other function, exactly as written.")
    kind: str = Field(description="lift or cardio.")
    category: str = Field(description="A lift: squat, hinge, push, pull, carry, core or accessory. Cardio: "
                                      "walk, run, cycle, row, swim, machine or other.")


class ExercisesResult(BaseModel):
    model_config = TOP

    exercises: list[ExerciseItem] = Field(description="Every exercise that matches, in list order.")


# ---------------------------------------------------------------- log_lifts

class SetOut(BaseModel):
    exercise: str = Field(description="The exercise name.")
    set_number: int = Field(description="The set's number within this exercise in this session, from 1.")
    weight_kg: float = Field(description="Weight in kg; 0 for bodyweight work.")
    reps: int = Field(description="Reps done.")


class Session(BaseModel):
    id: int = Field(description="The session's id.")
    date: str = Field(description="The day it happened, YYYY-MM-DD, on the person's own calendar.")
    notes: str = Field(description="The session's notes; empty when none.")
    logged_by: str = Field(description="Who logged it: claude or web.")
    created_at: str = Field(description="When it was logged, UTC.")
    updated_at: str = Field(description="When it last changed, UTC.")
    deleted_at: str | None = Field(description="When it was deleted, UTC, or null.")
    is_sample: bool = Field(description="True only for sample data.")
    sets: list[SetOut] = Field(description="Every set, in the order logged.")


class NewBest(BaseModel):
    exercise: str = Field(description="The lift.")
    kind: str = Field(description="estimated_max (estimated one-rep max) or weight (heaviest weight lifted).")
    value: float = Field(description="The new best, in kg.")
    previous: float = Field(description="The best before this session, in kg.")
    text: str = Field(description="The new best in words, ready to quote.")


class LiftLinks(BaseModel):
    day: str = Field(description="The page for the session's day.")
    progress: dict[str, str] = Field(description="The progress page of each lift in the session, by lift name.")


class LogLiftsResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was logged: lift_session.")
    id: int = Field(description="The session's id.")
    duplicate: bool = Field(
        description="True when the same session was already logged in the last 10 minutes. Then nothing was "
                    "written and this is the earlier session. Say so; send allow_repeat: true only when the "
                    "person confirms it was a separate session.")
    session: Session = Field(description="The session as saved.")
    new_bests: list[NewBest] = Field(
        description="New bests this session set, against every earlier set of the lift. A lift's first session "
                    "never sets one. Empty on a duplicate.")
    summary: str = Field(description="The session in one line, ready to quote: its new bests, or for a lift's "
                                     "first session the mark it sets (a first session is never a new best).")
    links: LiftLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present on a duplicate: what happened and what to do.")


# ---------------------------------------------------------------- log_bodyweight

class WeighIn(BaseModel):
    id: int = Field(description="The weigh-in's id.")
    date: str = Field(description="The day, YYYY-MM-DD, on the person's own calendar.")
    kg: float = Field(description="Body weight in kg.")
    logged_by: str = Field(description="Who logged it: claude or web.")
    created_at: str = Field(description="When it was logged, UTC.")
    updated_at: str = Field(description="When it last changed, UTC.")
    deleted_at: str | None = Field(description="When it was deleted or replaced, UTC, or null.")
    is_sample: bool = Field(description="True only for sample data.")


class DatedKg(BaseModel):
    date: str = Field(description="YYYY-MM-DD.")
    kg: float = Field(description="Body weight in kg.")


class BodyweightTrend(BaseModel):
    method: str = Field(
        description="How change_kg was worked out: '7-day average' (the average ending today against the one "
                    "ending 4 weeks ago), 'simple change' (latest minus first) or 'single weigh-in'.")
    change_kg: float | None = Field(description="The change in kg, signed; null with a single weigh-in.")
    now_avg: float | None = Field(description="The 7-day average ending today, for the 7-day average method.")
    then_avg: float | None = Field(description="The 7-day average ending 4 weeks ago, for the same method.")
    first: DatedKg = Field(description="The first weigh-in.")
    latest: DatedKg = Field(description="The latest weigh-in.")
    weigh_ins: int = Field(description="How many weigh-ins there are.")


class BodyweightLinks(BaseModel):
    progress: str = Field(description="The progress page.")


class LogBodyweightResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was logged: bodyweight.")
    id: int = Field(description="The weigh-in's id.")
    duplicate: bool = Field(
        description="True when the same weight on the same day was logged in the last 10 minutes. Then nothing "
                    "was written and this is the earlier weigh-in. Say so.")
    weigh_in: WeighIn = Field(description="The weigh-in as saved.")
    replaced: WeighIn | None = Field(
        description="The earlier weigh-in on the same day that this one replaced, or null. One weigh-in a day is "
                    "kept live; the replaced one is not erased.")
    recent: list[DatedKg] = Field(description="The latest weigh-ins, newest first, up to 5.")
    trend: BodyweightTrend | None = Field(description="How body weight is moving, or null with no weigh-ins.")
    how: str = Field(description="How the numbers are worked out.")
    links: BodyweightLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present on a duplicate or a replacement: what happened.")


# ---------------------------------------------------------------- set_goal

class Goal(BaseModel):
    id: int = Field(description="The goal's id. set_goal takes it as goal_id.")
    kind: str = Field(description="lift or bodyweight.")
    exercise_id: int | None = Field(description="The lift's id on the exercise list, for a lift goal.")
    exercise: str | None = Field(description="The lift's name, for a lift goal; null for body weight.")
    distance_km: float | None = Field(description="Not used by lift or body weight goals: null.")
    unit: str = Field(description="kg.")
    start_value: float = Field(description="Where the goal started, in kg. See get_progress for how it is measured.")
    start_date: str = Field(description="The date the goal was set, YYYY-MM-DD.")
    direction: str = Field(description="up or down. It never changes; to aim the other way, drop it and set a new one.")
    target_value: float | None = Field(
        description="The target in kg. A lift goal is an estimated one-rep max, up to 666.67 kg.")
    target_date: str | None = Field(description="The date to reach it by, YYYY-MM-DD.")
    status: str = Field(description="open, hit or dropped.")
    text: str = Field(description="The goal in the person's words; empty when none.")
    created_at: str = Field(description="When it was set, UTC.")
    updated_at: str = Field(description="When it last changed, UTC.")
    closed_at: str | None = Field(description="When it was marked hit or dropped, UTC, or null.")
    is_sample: bool = Field(description="True only for sample data.")


class GoalLinks(BaseModel):
    goals: str = Field(description="The goals page.")


class SetGoalResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was saved: goal.")
    id: int = Field(description="The goal's id.")
    goal: Goal = Field(description="The goal as saved.")
    progress: GoalNumbers | None = Field(description="Where the goal stands now; null for a goal that is not open.")
    warnings: list[str] = Field(description="Things to tell the person, such as an ignored value or a second open "
                                            "goal for the same lift. Empty when none.")
    links: GoalLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present when the goal is closed (hit or dropped).")


# ---------------------------------------------------------------- food

class FoodEntryOut(BaseModel):
    id: int = Field(description="The food entry's id.")
    date: str = Field(description="The day it was eaten, YYYY-MM-DD.")
    meal: str = Field(description="breakfast, lunch, dinner or snack.")
    description: str = Field(description="What was eaten.")
    kcal: float = Field(description="Its calories.")
    source: str = Field(
        description="Who supplied the number: you_said (the person), saved_food (the app, from a saved food), "
                    "usda (the USDA table) or claude_estimate (your estimate).")
    estimated: bool = Field(description="True when the number is your estimate.")
    saved_food_id: int | None = Field(description="The saved food it came from, or null.")
    servings: float | None = Field(description="Servings of the saved food, or null.")
    usda_fdc_id: int | None = Field(description="The USDA food it came from, or null.")
    grams: float | None = Field(description="Grams of the USDA food, or null.")


class FoodDay(BaseModel):
    total_kcal: float = Field(description="Everything eaten on the day, in kcal.")
    estimated_kcal: float = Field(description="How much of the total is your estimates, in kcal.")
    estimated_share_pct: float | None = Field(description="The estimated part as a percentage of the total.")
    target_kcal: int | None = Field(description="The daily calorie target, or null when none is set.")
    remaining_kcal: float | None = Field(description="Target minus total, or null without a target.")
    net_kcal: float | None = Field(
        description="Food minus cardio calories, or null unless the person has turned adding them back on.")


class FoodLinks(BaseModel):
    day: str = Field(description="The page for the day.")
    foods: str = Field(description="The saved foods page.")


class LogFoodResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was logged: food_entry.")
    ids: list[int] = Field(description="The entries' ids, in the order sent.")
    duplicate: bool = Field(
        description="True when the same set of foods was logged in the last 10 minutes. Then nothing was "
                    "written and these are the earlier entries. Say so.")
    entries: list[FoodEntryOut] = Field(description="The entries as saved, each with its tag.")
    day: FoodDay = Field(description="The day's totals after this call.")
    summary: str = Field(description="What was logged, in one line, ready to quote.")
    links: FoodLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(
        default=None, description="Present when an entry is your estimate, or on a duplicate: what to tell the person.")


class SavedFood(BaseModel):
    id: int = Field(description="The saved food's id: log_food takes it as saved_food_id.")
    name: str = Field(description="The food's name.")
    serving_label: str = Field(description="What one serving is.")
    kcal_per_serving: float = Field(description="Calories in one serving.")
    grams_per_serving: float | None = Field(description="Grams in one serving, or null.")


class SavedFoodLinks(BaseModel):
    foods: str = Field(description="The saved foods page.")


class SaveFoodResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was saved: saved_food.")
    id: int = Field(description="The saved food's id.")
    created: bool = Field(description="True when it is new; false when a food of the same name was updated.")
    food: SavedFood = Field(description="The saved food as it is now.")
    summary: str = Field(description="What was saved, in one line, ready to quote.")
    links: SavedFoodLinks = Field(description="Pages to offer the person. Put links last in a reply.")


class SavedFoodHit(SavedFood):
    times_logged: int = Field(description="How often the person has logged it.")
    last_logged: str | None = Field(description="When they last logged it, YYYY-MM-DD, or null.")


class RecentFood(BaseModel):
    description: str = Field(description="What the person logged before.")
    kcal: float = Field(description="Its calories then.")
    source: str = Field(description="you_said or claude_estimate: who supplied that number.")
    last_logged: str = Field(description="When it was last logged, YYYY-MM-DD.")
    times: int = Field(description="How often it was logged.")


class UsdaFood(BaseModel):
    fdc_id: int = Field(description="The USDA id: log_food takes it as usda_fdc_id, with grams.")
    description: str = Field(description="The USDA's name for it.")
    data_type: str = Field(description="Which USDA data set it comes from.")
    kcal_per_100g: float = Field(description="Calories in 100 g.")


class FindFoodResult(BaseModel):
    model_config = TOP

    query: str = Field(description="What was looked up.")
    saved: list[SavedFoodHit] = Field(description="The person's saved foods that match: log one by its id.")
    recent: list[RecentFood] = Field(
        description="The person's own earlier entries that match, most frequent first. Their numbers are what "
                    "was logged then: reuse one only as the person's number if they confirm it.")
    usda: list[UsdaFood] | None = Field(description="USDA matches, or null when the USDA lookup is off or failed.")
    usda_note: str | None = Field(description="Why there are no USDA matches, or null.")
    summary: str = Field(description="What was found, in one line.")


# ---------------------------------------------------------------- cardio

class Pace(BaseModel):
    min_per_km: str = Field(description="Minutes per km, as m:ss.")
    speed_kmh: float = Field(description="Speed in km/h.")


class CardioEstimate(BaseModel):
    kcal: int = Field(description="The estimated calories: MET x 3.5 x body weight in kg / 200 x minutes.")
    weight_kg: float = Field(description="The body weight used: the person's current body weight.")
    met: float = Field(description="The activity's MET value.")
    source: str = Field(description="Where the MET value comes from: the 2024 Adult Compendium of Physical "
                                    "Activities, with its code. Name it when you quote the estimate.")


class CardioWeek(BaseModel):
    minutes: float = Field(description="Cardio minutes in the 7 days ending today.")
    target_minutes: int | None = Field(description="The weekly cardio target, or null when none is set.")


class CardioLinks(BaseModel):
    day: str = Field(description="The page for the day.")
    progress: str = Field(description="The progress page.")


class LogCardioResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was logged: cardio_entry.")
    id: int = Field(description="The cardio entry's id.")
    duplicate: bool = Field(
        description="True when the same session was logged in the last 10 minutes. Then nothing was written. "
                    "Say so.")
    entry: dict = Field(description="The cardio entry as saved, with every stored field.")
    pace: Pace | None = Field(description="The pace, when a distance was given; else null.")
    estimate: CardioEstimate | None = Field(
        description="Spotter's calorie estimate, or null when there is no weigh-in to work it from.")
    machine_kcal: int | None = Field(description="The machine's or watch's number, as the person gave it, or null.")
    week: CardioWeek = Field(description="Cardio minutes in the 7 days ending today, against the weekly target.")
    summary: str = Field(description="The session in one line, ready to quote.")
    links: CardioLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present when there is no estimate, or on a duplicate.")


# ---------------------------------------------------------------- get_progress

class EstimatedMaxPoint(BaseModel):
    date: str = Field(description="The session's date, YYYY-MM-DD.")
    estimated_max_kg: float = Field(description="That session's best estimated one-rep max, in kg.")


class BestSet(BaseModel):
    weight_kg: float = Field(description="Weight in kg.")
    reps: int = Field(description="Reps.")
    date: str = Field(description="When, YYYY-MM-DD.")
    estimated_max_kg: float = Field(description="Its estimated one-rep max, in kg.")


class Best(BaseModel):
    date: str = Field(description="When it was set, YYYY-MM-DD.")
    kind: str = Field(description="estimated_max or weight (heaviest weight lifted).")
    value: float = Field(description="The new best, in kg.")
    previous: float = Field(description="The best before it, in kg.")


class LiftTrend(BaseModel):
    slope_per_week: float = Field(description="The change in estimated max per week over the last 6 weeks, in kg.")
    points_used: int = Field(description="How many sessions the trend used.")


class LiftDetail(BaseModel):
    exercise: str = Field(description="The lift.")
    sessions: int = Field(description="Sessions that include this lift, at any reps.")
    last_trained: str | None = Field(description="The last date it was trained, YYYY-MM-DD, or null.")
    estimated_max_over_time: list[EstimatedMaxPoint] = Field(
        description="One point per session with a set of 1 to 10 reps, oldest first.")
    current_estimated_max_kg: float | None = Field(
        description="The best session estimated max in the last 28 days, or the latest session's when none is "
                    "that recent. An estimate, not a tested max.")
    best_set: BestSet | None = Field(description="The set with the highest estimated max ever, or null.")
    bests: list[Best] = Field(description="Every new best, oldest first.")
    trend: LiftTrend | None = Field(description="The trend over the last 6 weeks, or null with under 4 points.")
    goal: GoalNumbers | None = Field(description="The first open goal for this lift, or null.")
    goals: list[GoalNumbers] = Field(description="Every open goal for this lift.")


class LiftLine(BaseModel):
    exercise: str = Field(description="The lift.")
    sessions: int = Field(description="Sessions that include it.")
    last_trained: str | None = Field(description="The last date it was trained, YYYY-MM-DD.")
    current_estimated_max_kg: float | None = Field(
        description="The best session estimated max in the last 28 days, or the latest session's when none is "
                    "that recent.")
    best_estimated_max_kg: float | None = Field(description="The best estimated max ever.")
    slope_per_week: float | None = Field(description="The trend in kg per week over the last 6 weeks, or null.")
    goal_status: str | None = Field(
        description="Its first open goal's status (reached, on track, behind or not enough data), or null with no "
                    "open goal.")


class ProgressLinks(BaseModel):
    progress_lift: str | None = Field(default=None, description="The lift's progress page (with an exercise).")
    progress: str | None = Field(default=None, description="The progress page (without an exercise).")


class CardioSession(BaseModel):
    id: int = Field(description="The cardio entry's id.")
    date: str = Field(description="The day, YYYY-MM-DD.")
    minutes: float = Field(description="How long it lasted.")
    distance_km: float | None = Field(description="The distance in km, or null.")
    pace: Pace | None = Field(description="The pace, when a distance was given; else null.")
    estimate: CardioEstimate | None = Field(description="Spotter's calorie estimate, or null with no weigh-in.")
    machine_kcal: int | None = Field(description="The machine's or watch's number, or null.")


class CardioProgress(BaseModel):
    exercise: str = Field(description="The cardio activity.")
    sessions: list[CardioSession] = Field(description="Its sessions, newest first.")
    week: CardioWeek = Field(description="Its minutes in the 7 days ending today, against the weekly target.")


class GetProgressResult(BaseModel):
    model_config = TOP

    today: str = Field(description="Today's date for the person, YYYY-MM-DD.")
    lift: LiftDetail | None = Field(default=None, description="With an exercise: that lift in full.")
    lifts: list[LiftLine] | None = Field(default=None, description="Without an exercise: one line per lift logged.")
    cardio: CardioProgress | None = Field(default=None, description="With a cardio activity: its sessions and week.")
    goals: list[GoalNumbers] | None = Field(default=None, description="Without an exercise: every open goal.")
    how: str = Field(description="How the numbers are worked out.")
    links: ProgressLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present when nothing is logged yet.")


# ---------------------------------------------------------------- update_settings

class SettingsLinks(BaseModel):
    settings: str = Field(description="The Settings page, where the person can see their settings.")


class UpdateSettingsResult(BaseModel):
    model_config = TOP

    time_zone: str | None = Field(description="The person's time zone after this call (an IANA name), or null if "
                                              "still unset.")
    today: str = Field(description="Today's date for the person after this call, YYYY-MM-DD.")
    changed: list[str] = Field(description="The settings this call changed, such as time_zone. Empty when the value "
                                           "was already set.")
    summary: str = Field(description="What changed, in one line, ready to quote.")
    links: SettingsLinks = Field(description="Pages to offer the person. Put links last in a reply.")


# ---------------------------------------------------------------- log_measurement

class MeasurementOut(BaseModel):
    id: int = Field(description="The measurement's id.")
    date: str = Field(description="The day, YYYY-MM-DD, on the person's own calendar.")
    kind: str = Field(description="waist, hips, chest, arm, thigh, steps, sleep_hours, resting_hr or water_ml.")
    value: float = Field(description="The value, in unit.")
    unit: str = Field(description="cm, steps, h, bpm or ml: fixed by the kind.")
    logged_by: str = Field(description="Who logged it: claude or web.")
    created_at: str = Field(description="When it was logged, UTC.")
    updated_at: str = Field(description="When it last changed, UTC.")
    deleted_at: str | None = Field(description="When it was deleted or replaced, UTC, or null.")
    is_sample: bool = Field(description="True only for sample data.")


class DatedValue(BaseModel):
    date: str = Field(description="YYYY-MM-DD.")
    value: float = Field(description="The value, in the kind's unit.")


class DayLink(BaseModel):
    day: str = Field(description="The page for the entry's day.")


class LogMeasurementResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="What was logged: measurement.")
    id: int = Field(description="The measurement's id.")
    duplicate: bool = Field(
        description="True when the same value of the same kind on the same day was logged in the last 10 minutes. "
                    "Then nothing was written and this is the earlier one. Say so.")
    measurement: MeasurementOut = Field(description="The measurement as saved.")
    replaced: MeasurementOut | None = Field(
        description="The earlier value of the same kind on the same day that this one replaced, or null. It is "
                    "kept, not erased: restore_entry brings it back.")
    recent: list[DatedValue] = Field(description="The latest values of this kind, newest first, up to 10.")
    summary: str = Field(description="The measurement in one line, ready to quote.")
    links: DayLink = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present on a duplicate or a replacement: what happened.")


# ---------------------------------------------------------------- edit_entry

class EditEntryResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="The kind of entry edited: lift_session, bodyweight or measurement.")
    id: int = Field(description="The entry's id. An edit never changes it.")
    changed: list[str] = Field(description="The fields this edit changed.")
    before: dict = Field(description="The entry as it was, with the fields of its kind.")
    after: dict = Field(description="The entry as it is now.")
    summary: str = Field(description="What changed, in one line, ready to quote.")
    links: DayLink = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(
        default=None,
        description="For a lift session: bests are worked out again from the whole history; get_progress shows "
                    "them.")


# ---------------------------------------------------------------- delete_entry and restore_entry

class DeleteEntryResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="The kind of entry deleted.")
    id: int = Field(description="The entry's id. restore_entry takes it, with the same kind.")
    deleted: bool = Field(description="True: the entry is deleted. Nothing is erased.")
    entry: dict = Field(description="The entry as it was, with the fields of its kind.")
    note: str | None = Field(default=None, description="How to undo it.")


class RestoreEntryResult(BaseModel):
    model_config = TOP

    kind: str = Field(description="The kind of entry restored.")
    id: int = Field(description="The entry's id.")
    restored: bool = Field(description="True: the entry is live again.")
    entry: dict = Field(description="The entry as it is now.")
    replaced: list[dict] = Field(
        description="Live entries for the same day that this one took the place of. They are set aside, not "
                    "erased: restore_entry with their id swaps them back. Empty when none.")
    note: str | None = Field(default=None, description="Present when a live entry was set aside.")


# ---------------------------------------------------------------- get_log

class LogSession(Session):
    new_bests: list[NewBest] = Field(
        description="New bests this session set, against every earlier set of the lift.")


class Meals(BaseModel):
    breakfast: list[FoodEntryOut] = Field(description="Breakfast entries.")
    lunch: list[FoodEntryOut] = Field(description="Lunch entries.")
    dinner: list[FoodEntryOut] = Field(description="Dinner entries.")
    snack: list[FoodEntryOut] = Field(description="Snacks.")


class LogFood(FoodDay):
    meals: Meals = Field(description="The day's food entries, by meal, each with its tag.")


class LogDay(BaseModel):
    date: str = Field(description="The day, YYYY-MM-DD.")
    sessions: list[LogSession] = Field(description="The lift sessions on this day, with their sets and new bests.")
    weigh_in: WeighIn | None = Field(description="The day's weigh-in, or null.")
    measurements: list[MeasurementOut] = Field(description="The day's measurements, one per kind at most.")
    food: LogFood = Field(description="The day's food by meal, with the day's totals.")
    cardio: list[dict] = Field(
        description="The day's cardio sessions, each with its activity, pace, estimate and the machine's number.")


class LogTotals(BaseModel):
    sessions: int = Field(description="Lift sessions in the range.")
    weigh_ins: int = Field(description="Weigh-ins in the range.")
    measurements: int = Field(description="Measurements in the range.")
    food_entries: int = Field(description="Food entries in the range.")
    cardio_entries: int = Field(description="Cardio sessions in the range.")


class LogLinks(BaseModel):
    day: str = Field(description="The page for the last day in the range.")
    progress: str = Field(description="The progress page.")


class GetLogResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", description="The first day shown, YYYY-MM-DD.")
    to: str = Field(description="The last day shown, YYYY-MM-DD.")
    days: list[LogDay] = Field(description="Every day in the range, newest first, empty days included.")
    totals: LogTotals = Field(description="How many entries of each kind the range holds.")
    summary: str = Field(description="The range in a few lines, ready to quote.")
    links: LogLinks = Field(description="Pages to offer the person. Put links last in a reply.")


RESULTS = {
    "whoami": WhoamiResult,
    "list_exercises": ExercisesResult,
    "log_lifts": LogLiftsResult,
    "log_bodyweight": LogBodyweightResult,
    "set_goal": SetGoalResult,
    "get_progress": GetProgressResult,
    "update_settings": UpdateSettingsResult,
    "get_log": GetLogResult,
    "log_measurement": LogMeasurementResult,
    "edit_entry": EditEntryResult,
    "restore_entry": RestoreEntryResult,
    "delete_entry": DeleteEntryResult,
    "find_food": FindFoodResult,
    "log_food": LogFoodResult,
    "save_food": SaveFoodResult,
    "log_cardio": LogCardioResult,
}
