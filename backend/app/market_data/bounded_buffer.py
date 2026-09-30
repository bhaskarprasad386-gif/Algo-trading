"""Bounded priority buffer for live market-data backpressure."""
from __future__ import annotations
from collections import deque
from enum import IntEnum
from queue import Empty
from threading import Condition
from time import monotonic
from typing import Generic,TypeVar
T=TypeVar("T")
class BufferPriority(IntEnum): NORMAL=0; CRITICAL=1
class BoundedPriorityBuffer(Generic[T]):
    def __init__(self,*,maxsize:int,critical_timeout_seconds=1.0,normal_timeout_seconds=.25):
        if isinstance(maxsize,bool) or not isinstance(maxsize,int) or maxsize<1:raise ValueError("maxsize must be a positive integer")
        if critical_timeout_seconds<=0 or normal_timeout_seconds<=0:raise ValueError("timeouts must be positive")
        self.maxsize=maxsize;self.critical_timeout_seconds=float(critical_timeout_seconds);self.normal_timeout_seconds=float(normal_timeout_seconds)
        self._critical=deque();self._normal=deque();self._condition=Condition();self._normal_drops=0
    def qsize(self):
        with self._condition:return len(self._critical)+len(self._normal)
    def put(self,item,*,priority=BufferPriority.NORMAL):
        if not isinstance(priority,BufferPriority):raise TypeError("priority must be BufferPriority")
        deadline=monotonic()+(self.critical_timeout_seconds if priority is BufferPriority.CRITICAL else self.normal_timeout_seconds)
        with self._condition:
            while len(self._critical)+len(self._normal)>=self.maxsize:
                left=deadline-monotonic()
                if left<=0:
                    if priority is BufferPriority.NORMAL:self._normal_drops+=1
                    return False
                self._condition.wait(left)
            (self._critical if priority is BufferPriority.CRITICAL else self._normal).append(item);self._condition.notify_all();return True
    def get(self,timeout=None):
        with self._condition:
            deadline=None if timeout is None else monotonic()+max(0,timeout)
            while not self._critical and not self._normal:
                if deadline is None:self._condition.wait()
                else:
                    left=deadline-monotonic()
                    if left<=0:raise Empty
                    self._condition.wait(left)
            item=(self._critical if self._critical else self._normal).popleft();self._condition.notify_all();return item
    def task_done(self):
        with self._condition:self._condition.notify_all()
    def snapshot(self):
        with self._condition:
            c=len(self._critical);n=len(self._normal);return {"queue_depth":c+n,"queue_capacity":self.maxsize,"queue_utilization":(c+n)/self.maxsize,"critical_depth":c,"normal_depth":n,"normal_drops":self._normal_drops}
