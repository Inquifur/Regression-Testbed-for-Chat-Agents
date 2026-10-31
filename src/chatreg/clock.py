"""只前进、不等待真实时间的业务时钟。"""


class VirtualClock:
    def __init__(self) -> None:
        self._now_ms = 0

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, at_ms: int) -> None:
        # bool 是 int 的子类，必须使用精确类型检查。
        if type(at_ms) is not int or at_ms < 0:
            raise ValueError("时间必须是非负整数毫秒，不能是布尔值或浮点数")
        if at_ms < self._now_ms:
            raise ValueError(f"虚拟时间不能倒退：当前 {self._now_ms}，目标 {at_ms}")
        self._now_ms = at_ms
