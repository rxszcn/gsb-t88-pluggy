# Probe old (2.8.0-era seed) pluggy: two parallel wrapper engines in
# src/pluggy/_callers.py with different rules. Run from repo root:
#   PYTHONPATH=src .venv/bin/python <this file>
import warnings

import pluggy

hookspec = pluggy.HookspecMarker("demo")
hookimpl = pluggy.HookimplMarker("demo")


class Spec:
    @hookspec
    def h(self):
        pass


def build():
    pm = pluggy.PluginManager("demo")
    pm.add_hookspecs(Spec)
    return pm


print("--- 1) old-style hookwrapper, plain impl returns 42 ---")
pm = build()


class Old:
    @hookimpl(hookwrapper=True)
    def h(self):
        outcome = yield
        outcome.get_result()  # classic old-style body, no return


class Plain:
    @hookimpl
    def h(self):
        return 42


pm.register(Old(), "old")
pm.register(Plain(), "plain")
print("result:", pm.hook.h())

print("--- 2) new-style wrapper that yields but forgets to return ---")
pm = build()


class NewForget:
    @hookimpl(wrapper=True)
    def h(self):
        yield


pm.register(NewForget(), "w")
pm.register(Plain(), "plain")
print("result:", pm.hook.h())

print("--- 3) new-style wrapper, explicit return passthrough ---")
pm = build()


class NewReturn:
    @hookimpl(wrapper=True)
    def h(self):
        res = yield
        return res + [99]


pm.register(NewReturn(), "w")
pm.register(Plain(), "plain")
print("result:", pm.hook.h())

print("--- 4) old-style hookwrapper trying to swallow an exception ---")
pm = build()


class OldSwallow:
    @hookimpl(hookwrapper=True)
    def h(self):
        outcome = yield
        try:
            outcome.get_result()
        except ValueError:
            pass  # "swallow" it


class Boom:
    @hookimpl
    def h(self):
        raise ValueError("boom")


pm.register(Boom(), "boom")
pm.register(OldSwallow(), "old")
try:
    print("result:", pm.hook.h())
except ValueError as e:
    print("raised anyway:", e)

print("--- 5) new-style wrapper swallowing the same exception ---")
pm = build()


class NewSwallow:
    @hookimpl(wrapper=True)
    def h(self):
        try:
            yield
        except ValueError:
            return "swallowed"


pm.register(Boom(), "boom")
pm.register(NewSwallow(), "new")
print("result:", pm.hook.h())

print("--- 6) old-style force_result vs new-style return on firstresult hook ---")


class Spec1:
    @hookspec(firstresult=True)
    def h(self):
        pass


pm = pluggy.PluginManager("demo")
pm.add_hookspecs(Spec1)


class OldForce:
    @hookimpl(hookwrapper=True)
    def h(self):
        outcome = yield
        outcome.force_result("forced-by-old")


pm.register(OldForce(), "old")
pm.register(Boom(), "boom")
try:
    print("result:", pm.hook.h())
except ValueError as e:
    print("raised:", e)
