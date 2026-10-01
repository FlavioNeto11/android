"""Daily spending cap for the dispatcher."""


class BudgetExceeded(Exception):
    pass


class BudgetMeter:
    def __init__(self, daily_cap: float, warn_ratio: float = 0.8) -> None:
        self.daily_cap = daily_cap
        self.warn_ratio = warn_ratio
        self.spent = 0.0

    def record(self, amount: float) -> None:
        self.spent += amount

    def check(self) -> str:
        """Return 'ok', 'warn' or raise when the cap is reached."""
        if self.daily_cap <= 0:
            return "ok"
        if self.spent >= self.daily_cap:
            raise BudgetExceeded(f"daily cap reached: {self.spent:.2f} of {self.daily_cap:.2f}")
        if self.spent >= self.daily_cap * self.warn_ratio:
            return "warn"
        return "ok"
