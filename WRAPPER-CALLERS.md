# 两套 Hook Wrapper 收尾语义对照（现状勘察，不改代码）

- 复现入口：`repro/wrapper-semantics.py`，跑法 `PYTHONPATH=src .venv/bin/python repro/wrapper-semantics.py`。
- 引擎文件：`src/pluggy/_callers.py`（唯一的 `_multicall` 循环）、`src/pluggy/_result.py`（`Result` 信封）、`src/pluggy/_hooks.py`（标记、校验与排序）、`src/pluggy/_manager.py`（注册校验）。
- 本文所有行号、读数均对应当前工作区；六段读数来自上述脚本，表格之外的补充读数是同版本下用一次性内联脚本追加的探针，未改动仓库任何文件。

## 一、六段读数与逐条归因

| 段 | 写法 | 读数 |
| --- | --- | --- |
| 1 | `hookwrapper=True`，收尾只调 `outcome.get_result()`，不写 return；普通实现返回 42 | `[42]` |
| 2 | `wrapper=True`，函数体只有 `yield`（忘记 return）；普通实现返回 42 | `None` |
| 3 | `wrapper=True`，`res = yield; return res + [99]`；普通实现返回 42 | `[42, 99]` |
| 4 | `hookwrapper=True`，`try: outcome.get_result() except ValueError: pass`；实现抛 `ValueError("boom")` | `raised anyway: boom` |
| 5 | `wrapper=True`，`try: yield except ValueError: return "swallowed"`；实现抛 `ValueError("boom")` | `swallowed` |
| 6 | firstresult 钩子；`hookwrapper=True` 里 `force_result("forced-by-old")`；实现抛 `ValueError` | `forced-by-old` |

**段 1：42 是谁交出去的。** 旧式包装器不直接进 `_multicall` 的收尾循环：`src/pluggy/_callers.py:104-108` 把它包进适配生成器 `run_old_style_hookwrapper`（`src/pluggy/_callers.py:25-53`），循环只跟适配器打交道。普通实现 `Plain.h` 在 `src/pluggy/_callers.py:121-123` 执行，42 进 `results=[42]`；`finally` 里 `src/pluggy/_callers.py:129-132` 把 `result` 定为列表 `[42]`；`src/pluggy/_callers.py:152` 的 `teardown.send(result)` 把 `[42]` 交给适配器；适配器在 `src/pluggy/_callers.py:38-39` 用它构造 `Result(res, None)` 信封，`src/pluggy/_callers.py:43` 再把信封 send 给用户生成器；用户那句 `outcome.get_result()`（`src/pluggy/_result.py:91-103`）在 `_exception is None` 时原样返回 `_result`，只是读；用户没写 return，生成器以 `StopIteration(value=None)` 结束，被 `src/pluggy/_callers.py:44-45` 的 `except StopIteration: pass` 无条件吞掉；最后适配器在 `src/pluggy/_callers.py:53` `return result.get_result()`——交回调用方的是信封里那份 `[42]`，与用户生成器 return 没 return 毫无关系。

**段 2：整个结果为什么变空。** 新式包装器是裸生成器，在 `src/pluggy/_callers.py:110-117` 直接进 `teardowns`，没有适配器、没有信封。同样 `result=[42]` 在 `src/pluggy/_callers.py:152` 被 send 进 `NewForget.h`，函数体在 `yield` 后直接结束，Python 令其抛出 `StopIteration(value=None)`；`src/pluggy/_callers.py:157-160` 的 `except StopIteration as si: result = si.value` 用停止值 `None` 覆盖了 `[42]`。42 不是丢在别处，就是被停止值顶掉的。

**段 3：** 同一段 2 的机制，停止值是显式返回的新列表，覆盖后读数 `[42, 99]`。注意是整体替换而非原地 append，外层若再有包装器，看到的是这个新对象。

**段 4：旧式为什么吞不掉异常。** `Boom.h` 在 `src/pluggy/_callers.py:121` 抛 ValueError，被 `src/pluggy/_callers.py:126-127` 收进 `exception`。收尾时 `src/pluggy/_callers.py:137-139` 对适配器 `teardown.throw(exception)`；适配器不松手，在 `src/pluggy/_callers.py:40-41` 把异常包成 `Result(None, exc)` 信封，`src/pluggy/_callers.py:43` 把信封 send 进用户生成器。用户调 `outcome.get_result()` 时，`src/pluggy/_result.py:102-103` 把信封里存的同一个异常 `raise exc.with_traceback(tb)` 抛出，用户的 `except ValueError: pass` 确实接住了——但接住的只是“读取”这一动作抛出的异常，`Result._exception` 没有被清（能清它的 API 只有 `force_result`/`force_exception`，`src/pluggy/_result.py:67-89`）。适配器走到 `src/pluggy/_callers.py:53` 第二次调用 `result.get_result()`，`src/pluggy/_result.py:103` 把同一个 ValueError 再抛一次；该异常从适配器冒出后被 `src/pluggy/_callers.py:161-163` 记回 `exception`，最终由 `src/pluggy/_callers.py:166-167` 重新抛出。一句话：用户的 try 包住的是 `get_result()` 的重读，真正的出口是适配器 `src/pluggy/_callers.py:53` 对同一信封的第二次 `get_result()`。

**段 5：新式为什么吞得掉。** `src/pluggy/_callers.py:139` 直接把 ValueError `throw` 进用户生成器本身（中间没有信封），异常在用户的 `yield` 处重抛，被用户自己的 `except ValueError` 接住，函数 `return "swallowed"`，生成器以 `StopIteration("swallowed")` 正常结束。`src/pluggy/_callers.py:157-160` 一次做两件事：`result = si.value` 装上 `"swallowed"`，`exception = None` 清掉待抛异常；`src/pluggy/_callers.py:166` 判空放行。吞异常的动作是用户的 except 加 return 完成的，清账是 `src/pluggy/_callers.py:159` 完成的。

## 二、两条收尾路径点名

**旧式（经适配器）最后一步取结果那一句：** `src/pluggy/_callers.py:53` 的 `return result.get_result()`。

语义：返回值的唯一来源是 `Result` 信封；信封在 `src/pluggy/_callers.py:38-41` 由进入适配器的结果或异常构造，此后只可能被 `force_result`/`force_exception` 改写。用户生成器自己 `return x` 会变成 StopIteration，在 `src/pluggy/_callers.py:44-45` 被无条件丢弃；用户生成器在收尾段 raise，则在 `src/pluggy/_callers.py:46-48` 经 `_warn_teardown_exception`（`src/pluggy/_callers.py:66-73`，发 `PluggyTeardownRaisedWarning`）后继续上抛。即“信封说了算，用户 return 说了不算”。

**新式把生成器停止值拿去覆盖结果的那两行：** `src/pluggy/_callers.py:157-160`：

```python
except StopIteration as si:
    result = si.value
    exception = None
```

语义：生成器停止时携带的值（函数 `return` 的值；没写 return 就是 `None`）无条件成为新的整体结果，同时清掉待抛异常——“返回值透传/改写”和“吞掉异常”是同一个动作的两面。若生成器在收尾段抛出别的异常，则走 `src/pluggy/_callers.py:161-163` 记为新的 `exception`，继续向外层 throw。

两套规则方向相反：旧式里用户不表态（不 return、不 force）时结果原样穿透；新式里用户不表态（不 return）等价于明确表态为 `None`，把结果清空。

## 三、firstresult 旋钮：硬压结果与返回值谁赢

脚本段 6 的读数：`forced-by-old`——这只是“唯一包装器”场景，回答不了“谁赢”。同一版本下补齐的读数（firstresult 钩子，普通实现 42 / Boom 抛 ValueError）：

| 场景 | 读数 |
| --- | --- |
| 只有新式 wrapper，`return "new-return:" + res` | `new-return:42` |
| 只有新式 wrapper，只 `yield` 不 return | `None` |
| 只有新式 wrapper，except 后 `return "swallowed"`（实现抛 ValueError） | `swallowed`（与段 6 同款“吞异常+定值”能力） |
| 旧式 force_result 与新式 return 同钩，注册顺序 old → new → plain | `new-return:forced-by-old`（`repr` 出的内层单引号，即 new 包着 old） |
| 同一组插件按 plain → new → old 注册 | `forced-by-old` |
| 两个新式 wrapper（W1、W2）+ plain，按 W1、W2 注册 | `W2(W1(42))` |
| 交换注册顺序 W2、W1 | `W1(W2(42))` |
| 非 firstresult 钩子里旧式 `force_result("forced")` | `forced`（信封原样返回字符串，引擎不替它包成列表） |

结论：不存在“按风格判优”。两条路径最后都落到 `src/pluggy/_callers.py:157-160` 的停止值覆盖——旧式的 `outcome.force_result(x)` 改的是信封（`src/pluggy/_result.py:67-78`，同时清空 `_exception`，这正是段 6 里 ValueError 被一起抹掉的原因），适配器 `src/pluggy/_callers.py:53` 再把它变成自己的 return；两者在循环看来都是停止值。决定胜负的只有层级：实现按 `src/pluggy/_hooks.py:401-408` 注释的顺序排列（非包装器在前，包装器按注册顺序在后），`src/pluggy/_callers.py:93` 反向迭代建栈，teardown 在 `src/pluggy/_callers.py:135` 再按栈顺序（= 最早注册的包装器最后收尾、最外层）执行；每出一个 teardown 就 `result = si.value` 覆盖一次，`src/pluggy/_callers.py:158` 无条件生效。所以最后完成收尾、最外层的包装器赢，与新旧无关——“old → new”注册时 new 最外层，读数包着 old 的值；反过来 old 最外层，读数 `forced-by-old`。

另有一个旋钮时点要分清：firstresult 的单值化发生在所有包装器收尾之前，即 `src/pluggy/_callers.py:124-125` 拿到第一个非 None 结果即停，随后 `src/pluggy/_callers.py:129-130` 取 `results[0]`；包装器收到、返回的都是这个单值。force_result/return 写什么引擎就传什么，`src/pluggy/_result.py:67-75` 的 docstring 只是文字上建议 firstresult 传单值、非 firstresult 传列表，没有运行时强制。

**文档与类型注解里写没写到这套差别？** 只写到了一半，而且是分散的：

- 写到的：`docs/index.rst:497` 明说“Old-style hook wrappers can **not** return results; they can only modify them using the `Result.force_result` API”；`docs/index.rst:500` 明说旧式收尾不应 raise，会导致后续 hookwrapper 被跳过；`docs/index.rst:429-435` 写明新式“Return a value … or raise an exception … The return value or exception propagate to further hook wrappers, and finally to the hook caller”；`src/pluggy/_hooks.py:240-242` 的 marker docstring 写“`wrapper` 函数的返回值成为钩子返回值”；`docs/index.rst:386-388` 只笼统声明两风格“fully interoperable”；`src/pluggy/_result.py:67-75` 与 `src/pluggy/_result.py:91-96` 的 docstring 各有一句 firstresult 传单值/否则传列表的提示；CHANGELOG 1.1.0（`CHANGELOG.rst:149-156`）记录新风格去掉 `Result` 对象、“expected to return a value or raise an exception”。
- 没写到的（四处关键差异均无成文）：新式包装器不 return 时隐式 `StopIteration(None)` 会清空既有结果（段 2 读数）；旧式里 `except: outcome.get_result()` 接住异常但不清 `_exception`，出口仍重抛（段 4 读数）；`src/pluggy/_callers.py:157-160` 停止值整体覆盖并同时清异常这条精确规则；新旧同钩时按层级而非按风格决胜负。类型层面同样没有约束：`src/pluggy/_callers.py:22` 的 `Teardown = Generator[None, object, object]` 对两套都成立，旧式“用户 return 必被丢弃”、新式“必须显式 return 否则变 None”在注解里完全看不出；`src/pluggy/_manager.py:357-375` 只校验“包装器必须是生成器函数”和“两选项互斥”，`wrapper=True` 与 firstresult 组合没有任何校验或警告（本仓库测试 `testing/test_invocations.py:126` `test_firstresult_definition` 还显式覆盖了该组合可用）。

## 四、收口：能不能并成一条，代价谁付

能并，而且引擎层面已经并了一大半：`_multicall` 只有一个收尾循环，`run_old_style_hookwrapper`（`src/pluggy/_callers.py:25-53`）就是把旧式的“信封协议”翻译成新式的“停止值协议”的垫片。要消掉双引擎，等于删掉垫片、只留一种协议，二选一：

- **并到新式（选这一边）。** 语义基准为“生成器 return 值/抛出异常即钩子结果，不 return 即为 None”。变化全部落在旧式用户头上：不写 return 不再原样透传（段 1 的 `[42]` 会变 `None`），`try/except` 包住 `get_result()` 不再需要二次出口（段 4 这种“吞不掉”的坑直接消失），`Result`/`force_result`/`force_exception` 及 `Result.get_result` 的公开 API、`PluggyTeardownRaisedWarning` 进入移除清单。仓库内要跟着变的用例（当前 `testing/` 下锚定旧式的 31 处 `hookwrapper=True` 里直接受影响的至少有）：`testing/test_multicall.py:79` `test_hookwrapper`（断言无 return 时 `res == [2]`、firstresult 时 `res == 2`，新语义下均为 `None`）、`testing/test_multicall.py:146` `test_hookwrapper_order`（断言 `res == []` 正是靠 `src/pluggy/_callers.py:44-45` 丢弃停止值）、`testing/test_multicall.py:251` `test_hookwrapper_exception`（依赖 `result.exception`/`result.excinfo`）、`testing/test_multicall.py:271` `test_hookwrapper_force_exception`、`testing/test_invocations.py:173` `test_firstresult_force_result_hookwrapper`、`testing/test_invocations.py:158` 的 Plugin5（在 `test_firstresult_definition` 内）、`testing/test_warnings.py:15` `test_teardown_raised_warning`，以及 `testing/test_result.py` 整文件；文档要删 `docs/index.rst:440-503` 的 “Old-style wrappers” 整节并改写 `src/pluggy/_hooks.py:244-252` 的 marker docstring。
- 并到旧式：要给所有新式用户强制套上信封，段 2/3/5 的读数全部回退（停止值不再覆盖、except return 不再吞异常），`testing/test_multicall.py:112` `test_wrapper`、`:321` `test_wrapper_exception`、`:447` `test_suppress_inner_wrapper_teardown_exc`、`testing/test_invocations.py:208` `test_firstresult_force_result` 等都得改。这等于推翻 1.1.0 引入新模型的决定（`CHANGELOG.rst:149-156`），只替已经在写新代码的人制造回退，没有收益。

**选择与代价归属：选新式。** 理由：它就是生成器的原生约定（`return`/raise 即结果），语义只有一条，不会再生产段 4 那种“接住了但没真吞掉”的二义性；垫片 `src/pluggy/_callers.py:25-53`、`src/pluggy/_result.py` 整个信封体系和 teardown 警告分支可一并删除，维护方只付一次性删代码和发废弃周期的成本；外部代价由旧式 hookwrapper 的使用方承担，迁移动作机械（`outcome = yield; outcome.force_result(x)` → `res = yield; return x`，`force_exception(e)` → `raise e`，吞异常 → except 后 return），且文档本就把旧标为兼容遗留（`docs/index.rst:446-448`）。反过来并旧式则由全部新式用户为老坑买单，不值得。在真正动手前，第三节列的四件“没写到”的差异应先补文档，避免迁移期双方都靠读 `_multicall` 源码确认行为。

## 附：验证方式与范围

- 读数：`PYTHONPATH=src .venv/bin/python repro/wrapper-semantics.py`，六段输出与第一节表格逐字一致。
- 测试：`PYTHONPATH=src .venv/bin/pytest -q`，改动前后各跑一次，均为 `124 passed`。
- 本次只新增本文件；`src/`、`testing/`、`repro/` 零改动。
