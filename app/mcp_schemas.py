"""The shapes of the six MCP functions' arguments and results.

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
    settings: str = Field(description="The Settings page, where the person sets their time zone.")
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
    claude_can: str = Field(description="What the person allows Claude to do: read, log, or log_delete.")
    links: WhoamiLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    sign_in: SignIn = Field(description="The sign-in this call came with.")
    time_zone_note: str | None = Field(
        default=None,
        description="Present only when no time zone is set: today is then the UTC date. Ask the person their "
                    "time zone and point them to links.settings.")


# ---------------------------------------------------------------- list_exercises

class ExerciseItem(BaseModel):
    name: str = Field(description="The name to use in every other function, exactly as written.")
    kind: str = Field(description="lift or cardio.")
    category: str = Field(description="squat, hinge, push, pull, carry, core or accessory.")


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
    summary: str = Field(description="The session in one line, with its new bests, ready to quote.")
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


class GetProgressResult(BaseModel):
    model_config = TOP

    today: str = Field(description="Today's date for the person, YYYY-MM-DD.")
    lift: LiftDetail | None = Field(default=None, description="With an exercise: that lift in full.")
    lifts: list[LiftLine] | None = Field(default=None, description="Without an exercise: one line per lift logged.")
    goals: list[GoalNumbers] | None = Field(default=None, description="Without an exercise: every open goal.")
    how: str = Field(description="How the numbers are worked out.")
    links: ProgressLinks = Field(description="Pages to offer the person. Put links last in a reply.")
    note: str | None = Field(default=None, description="Present when nothing is logged yet.")


RESULTS = {
    "whoami": WhoamiResult,
    "list_exercises": ExercisesResult,
    "log_lifts": LogLiftsResult,
    "log_bodyweight": LogBodyweightResult,
    "set_goal": SetGoalResult,
    "get_progress": GetProgressResult,
}
