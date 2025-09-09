import time
import weakref
import warnings
from typing import Callable, Literal
from abc import ABC, abstractmethod
from functools import wraps
from contextlib import nullcontext
from threading import Thread, RLock, Event

__all__ = ['AutoCleanupDict', 'AutoCleanupList', 'CleanupMode', 'DurationCleanup', 'CheckFuncCleanup', 'DurationAndCheckFuncCleanup']

def thread_lock(func):
    """ Internal wrapper. `self` must have `_lock` property. """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, "_lock"):
            raise AttributeError("`self` must have a `_lock` property!")
        with self._lock:
            return func(self, *args, **kwargs)
    return wrapper

class CleanupMode(ABC):
    """ Base class for the cleanup mode. """
    @abstractmethod
    def validate(self, item, timestamp, now):
        pass

    @abstractmethod
    def snapshot(self) -> dict:
        """ Convert into a snapshot (dict). """
        pass

    @abstractmethod
    def from_snapshot(data: dict):
        """ Load from a snapshot (dict). """
        pass

class DurationCleanup(CleanupMode):
    def __init__(self, duration: int | float):
        """
        `CleanupMode` dependent entirely on duration tracking.

        :param duration: The duration (in seconds).
        :type duration: int, float

        :raises ValueError: Invalid arg type.
        """
        if not isinstance(duration, (int, float)):
            raise ValueError("`duration` must be an `int` or `float`.")
        self.drt = duration

    def validate(self, _, timestamp, now):
        """
        Validates item data for cleanup.

        :param _: Placeholder param.
        :type _: `Any`

        :param timestamp: The timestamp of the item.
        :type timestamp: int, float

        :param now: The current time during processing.
        :type now: int, float

        :return: Whether the item is to be deleted or not.
        :rtype: bool
        """
        return timestamp + self.drt <= now
    
    def snapshot(self):
        """
        Convert into a snapshot (dict).

        :return: The snapshot.
        :rtype: dict
        """
        return {"_id": 0, "duration": self.drt}

    def from_snapshot(data: dict):
        """
        Load from a snapshot (dict).

        :param data: The snapshot to load from.
        :type data: dict

        :return: The loaded `CleanupMode`.
        :rtype: `DurationCleanup`

        :raises KeyError: Key not found in the specified `data`.
        """
        return DurationCleanup(data['duration'])
    
class CheckFuncCleanup(CleanupMode):
    def __init__(self, func: Callable):
        """
        `CleanupMode` dependent entirely on the specified `func`'s judgement.

        :param func: The judge who must take `(item, timestamp)` and return a `bool`.
                     - `True`: The item is to be deleted.
                     - `False`: The item is to be kept.
        :type func: Callable

        :raises ValueError: Invalid arg type.
        """
        if not callable(func):
            raise ValueError("`check_func` must be a callable func which returns a `bool`.")
        self.cf = func

    def validate(self, item, timestamp, _):
        """
        Validates item data for cleanup.

        :param item: The item to validate.
        :type item: `Any`

        :param timestamp: The timestamp of the item.
        :type timestamp: int, float

        :param _: Placeholder param.
        :type _: `Any`

        :return: Whether the item is to be deleted or not.
        :rtype: bool
        """
        cf_result = self.cf(item, timestamp)
        if not isinstance(cf_result, bool):
            raise RuntimeError(f"Received an unexpected response from custom `check_func`. Expected `bool`. Found `{cf_result.__class__.__name__}` instead.")
        return cf_result
    
    def snapshot(self) -> dict[str, Callable]:
        """
        Convert into a snapshot (dict). Returns the `func` as is.

        :return: The snapshot.
        :rtype: dict
        """
        return {"_id": 1, "func": self.cf}
    
    def from_snapshot(data: dict[str, Callable]):
        """
        Load from a snapshot (dict). Expects the Callable `func` to be present as is.

        :param data: The snapshot to load from.
        :type data: dict

        :return: The loaded `CleanupMode`.
        :rtype: `CheckFuncCleanup`

        :raises KeyError: Key not found in the specified `data`.
        """
        return CheckFuncCleanup(data['func'])
        
class DurationAndCheckFuncCleanup(CleanupMode):
    def __init__(self, duration: int | float, func: Callable, priority: Literal['DRT', 'CF', 'ANY'] = 'ANY'):
        """
        `CleanupMode` dependent on both duration tracking as well as the `func`'s judgement.

        :param duration: The duration (in seconds).
        :type duration: int, float

        :param func: The judge who must take `(item, timestamp)` and return a `bool` or a `tuple`.
                     - `bool`: `True` if the item is to be deleted and `False` if the item is to be kept.
                     - `tuple[bool, bool]`: The 1st index is for the standard cleanup judgement. The second bool is for override. If set to `True`, the item is immediately listed for deletion, irrespective of the other's judgement.
        :type func: Callable

        :param priority: Whose judgement to give priority to, defaults to 'ANY'.
                         - `DRT`: The duration and expiry is to be prioritized.
                         - `CF`: The `func`'s judgement is to be prioritized.
                         - `ANY`: (Default) Return `True` if either of the judges give the green light.
        :type priority: str, optional

        :raises ValueError: Invalid arg type.
        """
        if not isinstance(duration, (int, float)):
            raise ValueError("`duration` must be an `int` or `float`.")
        if not callable(func):
            raise ValueError("`check_func` must be a callable func which returns a `bool` or `tuple[bool, bool]`.")
        if priority not in ['DRT', 'CF', 'ANY']:
            raise ValueError(f"Invalid arg for `priority`. Expected 'DRT', 'CF' or 'ANY. Found '{priority}' instead.")
        
        self.drt = duration
        self.cf = func
        self.priority = priority

    def validate(self, item, timestamp, now):
        """
        Validates item data for cleanup.

        :param item: The item to validate.
        :type item: `Any`

        :param timestamp: The timestamp of the item.
        :type timestamp: int, float

        :param now: The current time during processing.
        :type now: int, float

        :return: Whether the item is to be deleted or not.
        :rtype: bool
        """
        expired = timestamp + self.drt <= now
        should_delete = False

        cf_result = self.cf(item, timestamp)
        if isinstance(cf_result, bool):
            should_delete = cf_result
        
        elif isinstance(cf_result, tuple) and len(cf_result) == 2 and isinstance(cf_result[0], bool) and isinstance(cf_result[1], bool):
            if cf_result[1] is True:
                return True
            should_delete = cf_result[0]

        if self.priority == 'ANY':
            return expired or should_delete
        elif self.priority == 'DRT':
            return expired
        else: # CF
            return should_delete
        
    def snapshot(self):
        """
        Convert into a snapshot (dict). Returns the `func` as is.

        :return: The snapshot.
        :rtype: dict
        """
        return {"_id": 2, "duration": self.drt, "func": self.cf, "priority": self.priority}
    
    def from_snapshot(data: dict):
        """
        Load from a snapshot (dict). Expects the Callable `func` to be present as is.

        :param data: The snapshot to load from.
        :type data: dict

        :return: The loaded `CleanupMode`.
        :rtype: `DurationAndCheckFuncCleanup`

        :raises KeyError: Key not found in the specified `data`.
        """
        return DurationAndCheckFuncCleanup(duration=data['duration'], func=data['func'], priority=data["priority"])

class AutoCleanupDict:
    def __init__(
            self,
            cleanup_mode: CleanupMode,
            cleanup_cooldown: int | float = 300,
            thread_timeout: int | float = 5,
            lock_free: bool = False
        ):
        """
        A `dict` which cleans up its items automatically according to the specified `cleanup_mode`.
        ⚠️Never modify `_items` directly. It may lead to unexpected behavior.

        :param cleanup_mode: The cleanup mode.
        :type cleanup_mode: `CleanupMode`

        :param cleanup_cooldown: The duration (in seconds) between each cleanup, defaults to `300`.
        :type cleanup_cooldown: int, float, optional

        :param thread_timeout: Timeout duration (in seconds) when joining the cleanup thread, defaults to `5`.
        :type thread_timeout: int,float, optional

        :param lock_free: Indicates whether to run without a lock or not, defaults to `False`.
        Please note that when set to `True`, the lock defaults to a `nullcontext` obj, providing unparallel direct access to the list and may cause unexpected behavior, obstructing the cleanup thread.

        :raises ValueError: Invalid arg type.
        """
        self.cm = cleanup_mode
        
        if not isinstance(cleanup_cooldown, (int, float)):
            raise ValueError("`cleanup_cooldown` must be an `int` or `float`")
        if not isinstance(thread_timeout, (int, float)):
            raise ValueError("`thread_timeout` must be an `int` or `float`")
       
        self.cd = cleanup_cooldown
        self.tt = thread_timeout

        self._items = {}
        self._lock = RLock() if not lock_free else nullcontext()
        self._cleanup_event = Event()
        self._cleanup_thread = Thread(target=self.__cleanup, daemon=True)
        self._cleanup_thread.start()

        self._finalizer = weakref.finalize(self, AutoCleanupDict._finalize_cleanup, self._cleanup_event, self._cleanup_thread, self.tt)
        
        self.clpf = 0 # cleanups performed
        self.itr = 0 # items removed
        self.lclp = time.time() # last cleanup performed

    @property
    @thread_lock
    def snapshot(self):
        """
        Convert into a snapshot (dict). Returns the `func` as is.

        :return: The snapshot.
        :rtype: dict
        """
        return {
            "items": self._items.copy(),
            "config": {
                "cleanup_mode": self.cm.snapshot(),
                "cleanup_cooldown": self.cd,
                "thread_timeout": self.tt,
                "clpf": self.clpf,
                "itr": self.itr,
                "lclp": self.lclp,
                "lock_free": isinstance(self._lock, nullcontext)
            }
        }
    
    @staticmethod
    def from_snapshot(data: dict, cleanup_mode: CleanupMode = None, load_items: bool = True):
        """
        Load from a snapshot (dict). Expects the Callable `func` to be present as is. '`_id`' should be present in the `data` or the `cleanup_mode` should be specified.

        :param data: The snapshot to load from.
        :type data: dict

        :param cleanup_mode: Fallsback to loading the specified `CleanupMode` when '`_id`' isn't present in the `data`, defaults to `None`.
        :type cleanup_mode: `CleanupMode`, optional

        :param load_items: Indicates whether to load the items from the `data` or not, defaults to `True`.
        :type load_items: `bool`, optional

        :return: The loaded `AutoCleanupDict`.
        :rtype: `AutoCleanupDict`

        :raises KeyError: Key not found in the specified `data`.
        """
        if not '_id' in data and cleanup_mode is None:
            raise ValueError("Data doesn't contain '_id' and `cleanup_mode` is not specified!")
        if data['_id'] == 0:
            cleanup_mode = DurationCleanup.from_snapshot(data['cleanup_mode'])
        elif data['_id'] == 1:
            cleanup_mode = CheckFuncCleanup.from_snapshot(data['cleanup_mode'])
        elif data['_id'] == 2:
            cleanup_mode = DurationAndCheckFuncCleanup.from_snapshot(data['cleanup_mode'])
        elif cleanup_mode is None:
            raise ValueError("Data doesn't contain a valid '_id' and no `cleanup_mode` is specified to fallback.")
        
        config = data['config']
        d = AutoCleanupDict(
            cleanup_mode=cleanup_mode,
            cleanup_cooldown=config['cleanup_cooldown'],
            thread_timeout=config['thread_timeout'],
            lock_free=data['lock_free']
        )
        d.clpf = config['clpf']
        d.itr = config['itr']
        d.lclp = config['lclp']

        if not load_items:
            return d

        for _, v in data['items'].items():
            if not isinstance(v[0], (int, float)):
                raise ValueError("The data may possibly be corrupted. Expected the value at pos 0 of value to be an `int` or `float`, corresponding to the timestamp.")
        
        d._items = data['items']
        return d

    @property
    def items(self):
        """ Provides a view into the internal `_items`. """
        return self._items
    
    @staticmethod
    def _finalize_cleanup(event: Event, thread: Thread, timeout: float):
        """
        Internal method to finalize the cleanup process and making sure the thread has ended. Called by the `finalizer` and `stop_cleanup`.

        :param event: The cleanup event.
        :type event: `threading.Event`

        :param thread: The cleanup thread.
        :type thread: `threading.Thread`

        :param timeout: The timeout duration (in seconds).
        :type timeout: float
        """
        event.set()
        thread.join(timeout=timeout)

    def __cleanup(self):
        """
        The function which repeatedly calls `sync_clean` at regular `cooldown`s till the `cleanup_event` is not set.
        """
        while not self._cleanup_event.wait(self.cd):
            self.sync_clean(supress=True)

    def sync_clean(self, supress: bool = False):
        """
        Runs a synchronous manual cleanup.

        :param supress: Indicates whether to supress the warning or not, defaults to `False`.
        :type supress: bool, optional

        :raises UserWarning: Indicates that a cleanup thread is already alive, and running a manual cleanup may cause unexpected behavior.
        """
        if not supress and self._cleanup_thread.is_alive():
            warnings.warn("A manual cleanup has been called while a cleanup thread is alive. It may cause unexpected behaviour.", UserWarning)

        with self._lock:
            now = time.time()
            keys_to_delete = [
                k for k, v in self._items.items() if self.cm.validate({k: v[1]}, v[0], now)
            ]

            for key in keys_to_delete:
                del self._items[key]

        self.clpf += 1
        self.itr += len(keys_to_delete)
        self.lclp = time.time()

    def cleanup_stats(self):
        """
        Returns the cleanup stats.

        :return: The cleanup stats.
        :rtype: dict
        """
        return {
            "cleanups_performed": self.clpf,
            "items_removed": self.itr,
            "last_cleanup_performed": self.lclp,
            "size": len(self)
        }

    def start_cleanup(self):
        """
        Start a new cleanup thread (if not running).

        :raises RuntimeError: Indicates that a cleanup thread is already running.
        """
        if self._cleanup_thread.is_alive():
            raise RuntimeError("A cleanup thread is already running!")
        
        self._cleanup_event = Event()
        self._cleanup_thread = Thread(target=self.__cleanup, daemon=True)
        self._cleanup_thread.start()

        self._finalizer = weakref.finalize(self, AutoCleanupDict._finalize_cleanup, self._cleanup_event, self._cleanup_thread, self.tt)

    def stop_cleanup(self):
        """
        Stops the cleanup thread if the `finalizer` and `cleanup_thread` are alive.
        """
        if self._finalizer.alive and self._cleanup_thread:
            self._finalize_cleanup(self._cleanup_event, self._cleanup_thread, self.tt)
            self._finalizer.detach()
    
    @thread_lock
    def update(self, m: dict):
        now = time.time()
        self._items.update({k: (now, v) for k, v in m.items()})

    @thread_lock
    def get(self, key, default = None):
        v = self._items.get(key, default)
        return v[1] if v is not None else default
    
    @thread_lock
    def __getitem__(self, key):
        v = self._items.get(key, None)
        if v is None:
            raise KeyError(key)
        return v[1]
    
    @thread_lock
    def __setitem__(self, key, value):
        self._items.update({key: (time.time(), value)})

    @thread_lock
    def __delitem__(self, key):
        del self._items[key]

    @thread_lock
    def __contains__(self, key):
        return key in self._items
        
    @thread_lock
    def setdefault(self, key, value):
        v = self._items.get(key, None)
        if v is None:
            self._items.update({key: (time.time(), value)})
            return value
        return v[1]
    
    @thread_lock
    def keys(self):
        return self._items.keys()
    
    @thread_lock
    def values(self):
        return (v[1] for v in list(self._items.values()))
    
    @thread_lock
    def clear(self):
        self._items.clear()

    @thread_lock
    def fromkeys(self, iterable, value = None):
        now = time.time()
        self._items.update({k: (now, value) for k in iterable})

    @thread_lock
    def pop(self, key, default = ...):
        try:
            i = self._items.pop(key)
        except KeyError:
            if default is ...:
                raise KeyError(key)
            return default
        return i[1]
    
    @thread_lock
    def copy(self):
        return AutoCleanupDict(
            cleanup_mode=self.cm,
            cleanup_cooldown=self.cd,
            thread_timeout=self.tt,
            lock_free=isinstance(self._lock, nullcontext)
        )
    
    @thread_lock
    def __repr__(self):
        return f"AutoCleanupDict({self._items})"

class AutoCleanupList:    
    def __init__(
            self,
            cleanup_mode: CleanupMode,
            cleanup_cooldown: int | float = 300,
            thread_timeout: float = 5,
            lock_free: bool = False
        ):
        """
        A `list` which cleans up its items automatically according to the specified `cleanup_mode`.
        ⚠️Never modify `_items` and `_timestamps` directly. It may lead to out-of-sync data and other unexpected behavior.

        :param cleanup_mode: The cleanup mode.
        :type cleanup_mode: `CleanupMode`

        :param cleanup_cooldown: The duration (in seconds) between each cleanup, defaults to `300`.
        :type cleanup_cooldown: int, float, optional

        :param thread_timeout: Timeout duration (in seconds) when joining the cleanup thread, defaults to `5`.
        :type thread_timeout: int,float, optional

        :param lock_free: Indicates whether to run without a lock or not, defaults to `False`.
        Please note that when set to `True`, the lock defaults to a `nullcontext` obj, providing unparallel direct access to the list and may cause unexpected behavior, obstructing the cleanup thread.

        :raises ValueError: Invalid arg type.
        """
        self.cm = cleanup_mode
        
        if not isinstance(cleanup_cooldown, (int, float)):
            raise ValueError("`cleanup_cooldown` must be an `int` or `float`")
        if not isinstance(thread_timeout, (int, float)):
            raise ValueError("`thread_timeout` must be an `int` or `float`")
        
        self.cd = cleanup_cooldown
        self.tt = thread_timeout

        self._items = []
        self._timestamps = []
        self._lock = RLock() if not lock_free else nullcontext()
        self._cleanup_event = Event()
        self._cleanup_thread = Thread(target=self.__cleanup, daemon=True)
        self._cleanup_thread.start()

        self._finalizer = weakref.finalize(self, AutoCleanupList._finalize_cleanup, self._cleanup_event, self._cleanup_thread, self.tt)

        self.clpf = 0 # cleanups performed
        self.itr = 0 # items removed
        self.lclp = 0 # last cleanup performed

    @property
    @thread_lock
    def snapshot(self):
        """
        Convert into a snapshot (dict). Returns the `func` as is.

        :return: The snapshot.
        :rtype: dict
        """
        return {
            "items": self._items,
            "timestamps": self._timestamps,
            "config": {
                "cleanup_mode": self.cm.snapshot(),
                "cleanup_cooldown": self.cd,
                "thread_timeout": self.tt,
                "clpf": self.clpf,
                "itr": self.itr,
                "lclp": self.lclp,
                "lock_free": isinstance(self._lock, nullcontext)
            }
        }
    
    @staticmethod
    def from_snapshot(data: dict, cleanup_mode: CleanupMode = None, load_items: bool = True):
        """
        Load from a snapshot (dict). Expects the Callable `func` to be present as is. '`_id`' should be present in the `data` or the `cleanup_mode` should be specified.

        :param data: The snapshot to load from.
        :type data: dict

        :param cleanup_mode: Fallsback to loading the specified `CleanupMode` when '`_id`' isn't present in the `data`, defaults to `None`.
        :type cleanup_mode: `CleanupMode`, optional

        :param load_items: Indicates whether to load the items from the `data` or not, defaults to `True`.
        :type load_items: `bool`, optional

        :return: The loaded `AutoCleanupDict`.
        :rtype: `AutoCleanupDict`

        :raises KeyError: Key not found in the specified `data`.
        """
        if not '_id' in data and cleanup_mode is None:
            raise ValueError("Data doesn't contain '_id' and `cleanup_mode` is not specified!")
        if data['_id'] == 0:
            cleanup_mode = DurationCleanup.from_snapshot(data['cleanup_mode'])
        elif data['_id'] == 1:
            cleanup_mode = CheckFuncCleanup.from_snapshot(data['cleanup_mode'])
        elif data['_id'] == 2:
            cleanup_mode = DurationAndCheckFuncCleanup.from_snapshot(data['cleanup_mode'])
        elif cleanup_mode is None:
            raise ValueError("Data doesn't contain a valid '_id' and no `cleanup_mode` is specified to fallback.")
        
        config = data['config']
        l = AutoCleanupList(
            cleanup_mode=cleanup_mode,
            cleanup_cooldown=config['cleanup_cooldown'],
            thread_timeout=config['thread_timeout'],
            lock_free=config['lock_free']
        )
        l.clpf = config['clpf']
        l.itr = config['itr']
        l.lclp = config['lclp']

        if not load_items:
            return l
        
        if len(data['timestamps']) != len(data['items']):
            raise ValueError("Mismatch between the length of the `timestamps` and `items`.")
        
        for i in data['timestamps']:
            if not isinstance(i, (int, float)):
                raise ValueError("Expected every value of `timestamps` to be an `int` or `float`.")
            
        return l

    @property
    def items(self):
        """ Provides a view into the internal `_items`. """
        return self._items
    
    @property
    def timestamps(self):
        """ Provides a view into the internal `_timestamps`. """
        return self._timestamps

    @staticmethod
    def _finalize_cleanup(event: Event, thread: Thread, timeout: float):
        event.set()
        thread.join(timeout=timeout)

    def start_cleanup(self):
        """
        Start a new cleanup thread (if not running).

        :raises RuntimeError: Indicates that a cleanup thread is already running.
        """
        if self._cleanup_thread.is_alive():
            raise RuntimeError("A cleanup thread is already running!")
        
        self._cleanup_event = Event()
        self._cleanup_thread = Thread(target=self.__cleanup, daemon=True)
        self._cleanup_thread.start()

        self._finalizer = weakref.finalize(self, AutoCleanupList._finalize_cleanup, self._cleanup_event, self._cleanup_thread, self.tt)

    def stop_cleanup(self):
        """
        Stops the cleanup thread if the `finalizer` and `cleanup_thread` are alive.
        """
        if self._finalizer.alive:
            self._finalize_cleanup(self._cleanup_event, self._cleanup_thread, self.tt)
            self._finalizer.detach()

    def validate(self): # Must be called from within a lock.
        """
        Validate the length of `_items` and `_timestamps`. 
        :TODO: `RLock._is_owned` is to be changed.

        :raises RuntimeError: Either the func was called by a thread which hadn't acquired the lock, or the timestamps are out of sync.
        """
        if isinstance(self._lock, RLock) and not self._lock._is_owned():
            raise RuntimeError("Func must be called from within an acquired lock!")
        if len(self._items) != len(self._timestamps):
            raise RuntimeError("Timestamps are out of sync! This may have been caused due to direct modification of `_items` or `_timestamps`.")
        
    def sync_clean(self, supress: bool = False):
        """
        Runs a synchronous manual cleanup.

        :param supress: Indicates whether to supress the warning or not, defaults to `False`.
        :type supress: bool, optional

        :raises UserWarning: Indicates that a cleanup thread is already alive, and running a manual cleanup may cause unexpected behavior.
        """
        if not supress and self._cleanup_thread.is_alive():
            warnings.warn("A manual cleanup has been called while a cleanup thread is alive. It may cause unexpected behaviour.", UserWarning)

        self.validate()

        with self._lock:
            now = time.time()
            self.validate()
            indexes_to_delete = [
                i for i in range(len(self._items)) if self.cm.validate(self._items[i], self._timestamps[i], now)
            ]

            for index in reversed(indexes_to_delete):
                del self._items[index]

        self.clpf += 1
        self.itr += len(indexes_to_delete)
        self.lclp = time.time()
            
    def cleanup_stats(self):
        """
        Returns the cleanup stats.

        :return: The cleanup stats.
        :rtype: dict
        """
        return {
            "cleanups_performed": self.clpf,
            "items_removed": self.itr,
            "last_cleanup_performed": self.lclp,
            "size": len(self)
        }

    def __cleanup(self):
        """
        The function which repeatedly calls `sync_clean` at regular `cooldown`s till the `cleanup_event` is not set.
        """
        while not self._cleanup_event.wait(self.cd):
            self.sync_clean(supress=True)

    @thread_lock
    def sort(self, *, key = None, reverse = False):
        """
        Sorts the list by mapping the items and timestamps, and then sorting the pairs according to the items.
        Then the sorted data is mapped back to `_items` and `_timestamps`.
        """
        pairs = list(zip(self._items, self._timestamps))

        def sort_key(pair):
            return pair[0] if key is None else key(pair[0])

        pairs.sort(key=sort_key, reverse=reverse)
        self._items, self._timestamps = map(list, zip(*pairs))

        self.validate()

    @thread_lock
    def insert(self, index, value):
        self._items.insert(index, value)
        self._timestamps.insert(index, time.time())

    @thread_lock
    def reverse(self):
        self._items.reverse()
        self._timestamps.reverse()

    @thread_lock
    def index(self, value):
        return self._items.index(value)

    @thread_lock
    def count(self, value):
        return self._items.count(value)
    
    @thread_lock
    def append(self, item):
        self._items.append(item)
        self._timestamps.append(time.time())
        self.validate()

    @thread_lock
    def remove(self, item):
        index = self._items.index(item)
        del self._items[index]
        del self._timestamps[index]
        self.validate()
    
    @thread_lock
    def clear(self):
        self._items.clear()
        self._timestamps.clear()

    @thread_lock
    def extend(self, l: list):
        now = time.time()
        self._items.extend(l)
        self._timestamps.extend([now] * len(l))
        self.validate()

    @thread_lock
    def pop(self, index: int):
        self._items.pop(index)
        self._timestamps.pop(index)
        self.validate()

    @thread_lock
    def copy(self):
        cl = AutoCleanupList(
            cleanup_mode=self.cm,
            cleanup_cooldown=self.cd,
            thread_timeout=self.tt,
            lock_free=isinstance(self._lock, nullcontext)
        )
        cl._items = self._items.copy()
        cl._timestamps = self._timestamps.copy()
        return cl
    
    @thread_lock
    def __contains__(self, item):
        return item in self._items
      
    @thread_lock
    def __getitem__(self, index):
        return self._items[index]
    
    @thread_lock
    def __setitem__(self, index, value):
        self._items[index] = value
        self._timestamps[index] = time.time()
        self.validate()

    @thread_lock
    def __delitem__(self, index):
        del self._items[index]
        del self._timestamps[index]
        self.validate()

    @thread_lock
    def __iter__(self):
        return iter(self._items)
    
    @thread_lock
    def __len__(self):
        return len(self._items)
    
    @thread_lock
    def __repr__(self):
        return f"AutoCleanupList({self._items})"
    
    @thread_lock
    def __bool__(self):
        return bool(self._items)
    
    def __enter__(self):
        return self
    
    def __exit__(self, t, v, tb):
        self.stop_cleanup()