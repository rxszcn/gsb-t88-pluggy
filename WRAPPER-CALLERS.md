# WRAPPER-CALLERS — 新旧两套包装器引擎的收尾规则现状

复现脚本：`PYTHONPATH=src .venv/bin/python repro/wrapper-semantics.py`（共六段读数）。
两套引擎都在 `src/pluggy/_callers.py` 一个文件里：旧式经 `run_old_style_hookwrapper()`
（`_callers.py:25`）适配后进 `_multicall()`（`_callers.py:76`），新式直接进 `_multicall()`。
收尾规则相反，下面逐条点名。

## 一、六段读数与原因

1. **旧式 hookwrapper，不返回任何东西，普通实现返回 42 → `result: [42]`**
   旧式包装器自己的返回值被丢弃：`run_old_style_hookwrapper()` 里
   `teardown.send(result)` 后内层生成器的 `StopIteration` 被 `_callers.py:44-45`
   的 `except StopIteration: pass` 吃掉。真正说了算的是 `_callers.py:53`
   `return result.get_result()`——适配器替插件把 `Result` 对象里的 `[42]` 交出来，
   与插件写不写 `return` 无关。

2. **新式 wrapper，只 `yield` 不写返回值 → `result: None`**
   新式生成器函数体走完、没有 `return`，`StopIteration.value` 就是 `None`。
   `_multicall()` 的收尾循环在 `_callers.py:157-158` 用 `result = si.value`
   无条件覆盖当前结果，于是 `[42]` 被抹成 `None`。新式里"忘了 return"
   等价于"return None"，而 `None` 也是合法覆盖值。

3. **新式 wrapper 显式 `return res + [99]` → `result: [42, 99]`**
   同上一条路径，`StopIteration.value == [42, 99]`，经 `_callers.py:158` 成为最终结果。

4. **旧式 hookwrapper 想用 try/except 吞掉 ValueError → `raised anyway: boom`（吞不掉）**
   异常路径：`_multicall()` 在 `_callers.py:139` `teardown.throw(exception)` 把异常
   扔进适配器，被 `_callers.py:40-41` 捕获成 `Result(None, exc)`，再于 `_callers.py:43`
   `teardown.send(result)` 交给插件生成器。插件里 `outcome.get_result()` 抛出的
   `ValueError` 确实被插件自己的 `except ValueError: pass` 接住了——但那只改了插件
   内部的控制流，`Result` 对象里的 `_exception` 还在。插件生成器结束后，适配器在
   `_callers.py:53` 执行 `return result.get_result()`，`Result.get_result()`
   （`src/pluggy/_result.py:91-103`）发现 `_exception` 非空，原样重抛。异常从
   `_multicall()` 的 `teardown.send/throw` 里冒出来，被 `_callers.py:161-162`
   收回 `exception`，最终 `_callers.py:166-167` 抛给调用方。
   旧式想吞异常，唯一办法是调 `Result.force_result()`（`_result.py:67-78`，
   会把 `_exception` 清成 `None`），try/except 不算数。

5. **新式 wrapper 吞掉同一个 ValueError → `result: swallowed`（能吞）**
   `_multicall()` 在 `_callers.py:139` `teardown.throw(exception)` 直接把异常抛进
   新式生成器的 `yield` 点，插件 `except ValueError: return "swallowed"` 接住并返回，
   `StopIteration.value == "swallowed"`。`_callers.py:158-159` 执行
   `result = si.value` 同时 `exception = None`——吞异常是新式收尾循环的原生语义，
   返回值落袋、异常清零。

6. **firstresult 钩子上旧式 `force_result` 对阵会抛异常的实现 → `result: forced-by-old`**
   `Boom.h()` 在 `_multicall.py` 的 setup 循环里抛 `ValueError`，`_callers.py:126-127`
   记入 `exception`；firstresult 下 `results` 为空，`_callers.py:129-130` 把 `result`
   置为 `None`。收尾时异常经适配器变成 `Result(None, ValueError)` 送进 `OldForce`，
   `outcome.force_result("forced-by-old")`（`_result.py:67-78`）一步把 `_result`
   改写、`_exception` 清零，`_callers.py:53` 返回 `"forced-by-old"`，
   经 `_callers.py:158-159` 落袋。异常被 `force_result` 合法删除。

## 二、两条收尾路径点名

- **旧式最后一步**：`src/pluggy/_callers.py:53`，`run_old_style_hookwrapper()` 的
  `return result.get_result()`。语义：包装器插件自己的返回值一律作废，最终结果以
  `Result` 对象的内部状态为准——`_exception` 还在就重抛（`_result.py:103`），
  否则交出 `_result`。想改结果只能走 `Result.force_result()` / `Result.force_exception()`。
- **新式停止时带值覆盖结果**：`src/pluggy/_callers.py:157-159`，`_multicall()` 收尾循环里
  `except StopIteration as si: result = si.value; exception = None`。语义：生成器停止时
  携带的值（即包装器的 `return` 值，缺省为 `None`）无条件成为新结果，并把未决异常清零。
  这两行对旧式同样生效——旧式适配器的 `return result.get_result()` 正是变成适配器
  生成器的 `StopIteration.value` 在这里落袋的；区别在于旧式插件体的返回值进不了这两行，
  新式插件体的返回值直接进这两行。

## 三、firstresult 旋钮下谁赢

读数（第六段 + 同环境补充实验，firstresult 钩子、普通实现返回 42）：

- 旧式 `force_result("forced-by-old")` 对阵抛异常的实现：**`forced-by-old`**，异常被删。
- 旧式 `force_result` 对阵新式 `return "returned-by-new"`：**后注册者赢**。
  先注册旧式后注册新式 → `returned-by-new`；先注册新式后注册旧式 → `forced-by-old`。
  机制：`_multicall()` 在 `_callers.py:135` `for teardown in reversed(teardowns)`
  逆序跑收尾，每个停止的包装器都在 `_callers.py:158` 覆盖一次 `result`，
  最后跑的那个（后注册、最外层）留下最终值。所以这不是新旧之争，是注册顺序之争，
  两种风格的"最后一句话"都汇入同一行 `result = si.value`。
- 新式只 `yield` 不 `return`（firstresult）→ `None`；旧式只看不碰（firstresult）→ `42`。

文档与类型注解里的记载：

- `docs/index.rst:622`："Note that all hook wrappers are still invoked with the first
  result."——只说了 firstresult 下包装器仍会被调用，没说新旧收尾差异。
- `HookimplMarker.__call__` 的 `wrapper` 参数文档（`src/pluggy/_hooks.py:240-242`）：
  "The return value of the function becomes the return value of the hook"——新式语义有写。
- `docs/index.rst:497-498`：旧式 "can **not** return results; they can only modify them
  using the `force_result` API"——旧式语义有写。
- `Result.force_result()` 文档字符串（`src/pluggy/_result.py:74`）："This overrides any
  previous result or exception."——`force_result` 删异常有写。
- **没有任何一处**（文档或类型注解）写到：新旧包装器同台时由注册顺序决定最终值、
  旧式 try/except 吞不掉异常而新式能吞、以及新式忘写 `return` 会把结果抹成 `None`。
  类型上 `wrapper`/`hookwrapper` 都只是 `bool` 标志（`_hooks.py:59-63`），
  收尾差异在注解层面不可见。

## 四、收口：能不能并成一条

结构上**已经并成一条了**：旧式靠 `run_old_style_hookwrapper()` 这个适配器跑在
`_multicall()` 同一套收尾循环里，没有第二个调用引擎。剩下的分歧只是"最后一句话"的
语义：旧式以 `Result` 对象状态为准（`_callers.py:53`），新式以 `return` 值为准
（`_callers.py:158`）。

如果把语义也并掉，只能二选一，且都有现存用例要改收尾：

- **并到新式语义**（return 值说了算）：第一段 `[42]` 变 `None`（旧式包装器都不写
  return），第四段 `raised anyway` 变 `swallowed`（try/except 突然能吞异常）。
  `testing/test_multicall.py:79` `test_hookwrapper`、`testing/test_multicall.py:251`
  `test_hookwrapper_exception`、`testing/test_invocations.py:173`
  `test_firstresult_force_result_hookwrapper` 这一批旧式用例的断言全部要翻。
  代价由所有现存旧式插件（pytest 生态）支付，且是静默行为变化。
- **并到旧式语义**（Result 对象说了算）：第二段 `None` 变回 `[42]`，第五段吞异常失效。
  新式是 1.1 起文档推荐的写法（`docs/index.rst:380-388`），等于开倒车。

**我的选择：维持现状，谁也不并。** 适配器模式已经把引擎收敛成一份，分歧只留在
两种风格各自的契约里，且各自文档（`_hooks.py:233-252`、`docs/index.rst:497-498`）
都写明了。真要动，就把第三节列出的三条未记载行为补进文档，而不是改引擎——
改引擎的代价由下游插件作者付，补文档的代价由本仓库付。
