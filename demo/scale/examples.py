"""Worked examples computed by code, used to give the scale corpus realistic volume.

Every number is derived with exact fractions, so the text is correct by construction
and the same seed always yields the same examples. They are practice material, not a
substitute for the hand-written explanations next to them.
"""
from fractions import Fraction
import random


def num(value):
    """Exact decimal when one exists, otherwise a fraction."""
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    scaled, places = value, 0
    while scaled.denominator != 1 and places < 6:
        scaled, places = scaled * 10, places + 1
    if scaled.denominator == 1:
        return f'{float(value):.{places}f}'
    return f'{value.numerator}/{value.denominator}'


def approx(value, places=4):
    value = Fraction(value)
    exact = num(value)
    return ('= ' + exact) if '/' not in exact and len(exact.partition('.')[2]) <= places else f'≈ {float(value):.{places}f}'


def paren(value):
    return f'({num(value)})' if Fraction(value) < 0 else num(value)


def add(rng):
    x, a = rng.randint(-9, 20), rng.randint(2, 15)
    return f'解方程 x + {a} = {x + a}。两边同时减去 {a}，得 x = {x + a} - {a} = {x}。检验：{paren(x)} + {a} = {x + a}，左右相等。'


def mul(rng):
    x, a = rng.choice([n for n in range(-8, 13) if n]), rng.randint(2, 9)
    return f'解方程 {a}x = {a * x}。两边同时除以 {a}，得 x = {paren(a * x)} ÷ {a} = {x}。检验：{a} × {paren(x)} = {a * x}，左右相等。'


def two_step(rng):
    x, a, b = rng.randint(-6, 12), rng.randint(2, 7), rng.randint(1, 12)
    c = a * x + b
    return (f'解方程 {a}x + {b} = {c}。两边同时减去 {b}（移项），得 {a}x = {c} - {b} = {c - b}；'
            f'两边同时除以 {a}，得 x = {x}。检验：{a} × {paren(x)} + {b} = {c}，左右相等。')


def both_sides(rng):
    x, c, extra, b = rng.randint(-5, 10), rng.randint(1, 5), rng.randint(1, 4), rng.randint(1, 9)
    a = c + extra
    d = a * x + b - c * x
    return (f'解方程 {a}x + {b} = {c}x + {paren(d)}。把含 x 的项移到左边、常数项移到右边：{a}x - {c}x = {paren(d)} - {b}；'
            f'合并同类项得 {extra}x = {d - b}；系数化为 1 得 x = {x}。'
            f'检验：左边 {a} × {paren(x)} + {b} = {a * x + b}，右边 {c} × {paren(x)} + {paren(d)} = {c * x + d}，左右相等。')


def brackets(rng):
    x, a, b = rng.randint(-4, 9), rng.randint(2, 6), rng.randint(1, 8)
    c = a * (x + b)
    return (f'解方程 {a}(x + {b}) = {c}。去括号，括号外的 {a} 与括号内每一项相乘：{a}x + {a * b} = {c}；'
            f'移项得 {a}x = {c} - {a * b} = {c - a * b}；系数化为 1 得 x = {x}。检验：{a} × ({paren(x)} + {b}) = {c}，左右相等。')


def fraction(rng):
    a, b, c = rng.randint(2, 6), rng.randint(1, 6), rng.randint(2, 9)
    x = a * (c - b)
    return (f'解方程 x/{a} + {b} = {c}。两边同时乘 {a} 去分母，常数项也要乘：x + {a * b} = {a * c}；'
            f'移项得 x = {a * c} - {a * b} = {x}。检验：{paren(x)}/{a} + {b} = {c}，左右相等。')


def decimal(rng):
    p, x, b = rng.choice([Fraction(1, 5), Fraction(1, 2), Fraction(2, 5), Fraction(3, 2)]), rng.randint(2, 12), Fraction(rng.randint(3, 40), 10)
    c = p * x + b
    return (f'解方程 {num(p)}x + {num(b)} = {num(c)}。两边同时乘 10，把小数化成整数：{num(p * 10)}x + {num(b * 10)} = {num(c * 10)}；'
            f'移项得 {num(p * 10)}x = {num(c * 10 - b * 10)}；系数化为 1 得 x = {x}。检验：{num(p)} × {x} + {num(b)} = {num(c)}，左右相等。')


def sum_word(rng):
    x, k, m = rng.randint(6, 30), rng.randint(2, 4), rng.randint(1, 9)
    total = x + k * x + m
    return (f'两个数的和是 {total}，较大的数比较小的数的 {k} 倍多 {m}，求这两个数。设较小的数为 x，则较大的数为 {k}x + {m}。'
            f'列方程 x + {k}x + {m} = {total}，合并得 {k + 1}x = {total - m}，解得 x = {x}，较大的数为 {k * x + m}。'
            f'检验：{x} + {k * x + m} = {total}，并且 {k} × {x} + {m} = {k * x + m}，符合题意。')


def motion(rng):
    v1, v2, t = rng.randrange(40, 90, 5), rng.randrange(30, 80, 5), rng.randint(2, 6)
    distance = (v1 + v2) * t
    return (f'两地相距 {distance} 千米，甲车每小时行 {v1} 千米，乙车每小时行 {v2} 千米，两车同时从两地出发相向而行，几小时后相遇？'
            f'设 x 小时后相遇，两车路程之和等于两地距离：{v1}x + {v2}x = {distance}，合并得 {v1 + v2}x = {distance}，解得 x = {t}。'
            f'检验：{v1} × {t} + {v2} × {t} = {distance}，符合题意。')


DIE_EVENTS = [('点数为偶数', {2, 4, 6}), ('点数为奇数', {1, 3, 5}), ('点数大于 4', {5, 6}), ('点数不超过 2', {1, 2}),
              ('点数是 3 的倍数', {3, 6}), ('点数小于 5', {1, 2, 3, 4}), ('点数为质数', {2, 3, 5})]


def dice(rng):
    (name_a, a), (name_b, b) = rng.sample(DIE_EVENTS, 2)
    both, either = len(a & b), len(a | b)
    relation = '两事件互斥，不可能同时发生。' if not both else '两事件可以同时发生，不互斥。'
    return (f'掷一枚均匀的骰子，样本空间有 6 个等可能的结果。A 为「{name_a}」，B 为「{name_b}」。'
            f'P(A) = {len(a)}/6，P(B) = {len(b)}/6，A∩B 含 {both} 个结果，P(A∩B) = {both}/6。'
            f'由加法公式，P(A∪B) = {len(a)}/6 + {len(b)}/6 - {both}/6 = {either}/6。A 不发生的概率为 1 - {len(a)}/6 = {6 - len(a)}/6。{relation}')


def conditional(rng):
    n11, n12, n21, n22 = (rng.randrange(10, 60, 5) for _ in range(4))
    total, b, a = n11 + n12 + n21 + n22, n11 + n21, n11 + n12
    return (f'调查 {total} 件产品：A 表示「来自甲车间」，B 表示「检出瑕疵」。甲车间有瑕疵的 {n11} 件、无瑕疵的 {n12} 件；'
            f'其他车间有瑕疵的 {n21} 件、无瑕疵的 {n22} 件。有瑕疵的共 {n11} + {n21} = {b} 件，'
            f'所以 P(A|B) = {n11}/{b} {approx(Fraction(n11, b))}；甲车间共 {n11} + {n12} = {a} 件，所以 P(B|A) = {n11}/{a} {approx(Fraction(n11, a))}。'
            f'两个条件概率的分母不同，方向不能互换。')


def multiplication(rng):
    good, bad = rng.randint(6, 15), rng.randint(2, 5)
    n = good + bad
    result = Fraction(good, n) * Fraction(good - 1, n - 1)
    return (f'一批产品共 {n} 件，其中合格品 {good} 件、次品 {bad} 件，不放回地依次抽取两件。第一件合格的概率是 {good}/{n}；'
            f'在第一件合格的条件下，剩下 {n - 1} 件中有 {good - 1} 件合格，第二件合格的条件概率是 {good - 1}/{n - 1}。'
            f'由乘法公式，两件都合格的概率为 {good}/{n} × {good - 1}/{n - 1} {approx(result)}。')


def independence(rng):
    pa, pb = Fraction(rng.choice([3, 4, 5, 6]), 10), Fraction(rng.choice([2, 4, 5]), 10)
    independent = rng.random() < 0.5
    pab = pa * pb if independent else pa * pb + Fraction(rng.choice([-5, 5]), 100)
    verdict = '两者相等，A 与 B 相互独立。' if independent else '两者不相等，A 与 B 不独立。'
    return (f'已知 P(A) = {num(pa)}，P(B) = {num(pb)}，P(A∩B) = {num(pab)}。判断独立性要比较 P(A∩B) 与 P(A) × P(B)：'
            f'{num(pa)} × {num(pb)} = {num(pa * pb)}，而 P(A∩B) = {num(pab)}，{verdict}'
            f'这里 P(A∩B) 大于 0，说明两事件可以同时发生，所以它们不互斥。')


def _sources(rng, count=None, rare=False):
    count = count or rng.choice([2, 3])
    if rare:
        priors = [Fraction(rng.choice([1, 2, 5]), 100)]
        priors.append(1 - priors[0])
    elif count == 2:
        first = Fraction(rng.choice([2, 3, 4, 6, 7]), 10)
        priors = [first, 1 - first]
    else:
        first, second = Fraction(rng.choice([2, 3, 5]), 10), Fraction(rng.choice([2, 3]), 10)
        priors = [first, second, 1 - first - second]
    likelihoods = ([Fraction(rng.choice([90, 95, 98]), 100), Fraction(rng.choice([2, 5, 8]), 100)] if rare
                   else rng.sample([Fraction(k, 100) for k in range(1, 10)], len(priors)))
    return priors, likelihoods


def _total(priors, likelihoods):
    joint = [p * q for p, q in zip(priors, likelihoods)]
    terms = ' + '.join(f'{num(p)} × {num(q)}' for p, q in zip(priors, likelihoods))
    return joint, sum(joint), terms


def total(rng):
    priors, likelihoods = _sources(rng)
    joint, evidence, terms = _total(priors, likelihoods)
    listing = '，'.join(f'P(A{i}) = {num(p)}、P(B|A{i}) = {num(q)}' for i, (p, q) in enumerate(zip(priors, likelihoods), 1))
    return (f'产品来自 {len(priors)} 条产线，它们互斥且完备：{listing}，各先验之和为 1。'
            f'由全概率公式，P(B) = {terms} = {num(evidence)}。'
            f'结果介于最小的次品率 {num(min(likelihoods))} 与最大的次品率 {num(max(likelihoods))} 之间，符合加权平均的含义。')


def bayes(rng, rare=False):
    priors, likelihoods = _sources(rng, rare=rare)
    joint, evidence, terms = _total(priors, likelihoods)
    listing = '，'.join(f'P(A{i}) = {num(p)}、P(B|A{i}) = {num(q)}' for i, (p, q) in enumerate(zip(priors, likelihoods), 1))
    posts = '，'.join(f'P(A{i}|B) = {num(j)} / {num(evidence)} {approx(j / evidence)}' for i, j in enumerate(joint, 1))
    counts = '、'.join(f'A{i} 约 {num(j * 10000)} 件' for i, j in enumerate(joint, 1))
    lead = '某类来源很少见（基础率低），检测并不完美：' if rare else '来源互斥且完备：'
    return (f'{lead}{listing}。先用全概率公式求分母：P(B) = {terms} = {num(evidence)}。'
            f'再由贝叶斯公式：{posts}。各后验之和为 1。'
            f'用 10000 件的假设频数复核：出现 B 的共约 {num(evidence * 10000)} 件，其中 {counts}，比例与上面的后验一致。')


def base_rate(rng):
    return bayes(rng, rare=True)


def sensitivity(rng):
    likelihoods = rng.sample([Fraction(k, 100) for k in range(1, 10)], 2)
    first, second = rng.sample([Fraction(k, 10) for k in (2, 3, 5, 6, 8)], 2)
    parts = []
    for prior in (first, second):
        joint, evidence, terms = _total([prior, 1 - prior], likelihoods)
        parts.append(f'先验为 {num(prior)} 与 {num(1 - prior)} 时，P(B) = {terms} = {num(evidence)}，'
                     f'P(A1|B) = {num(joint[0])} / {num(evidence)} {approx(joint[0] / evidence)}')
    return (f'两条产线的次品率保持不变：P(B|A1) = {num(likelihoods[0])}，P(B|A2) = {num(likelihoods[1])}，只改变产量比例。'
            f'{parts[0]}；{parts[1]}。似然没有变化，后验随先验改变，说明结论依赖于对总体比例的了解。')


GENERATORS = dict(add=add, mul=mul, two_step=two_step, both_sides=both_sides, brackets=brackets, fraction=fraction,
                  decimal=decimal, sum_word=sum_word, motion=motion, dice=dice, conditional=conditional,
                  multiplication=multiplication, independence=independence, total=total, bayes=bayes,
                  base_rate=base_rate, sensitivity=sensitivity)


def worked_examples(kind, count, seed):
    rng = random.Random(f'{kind}:{seed}')
    return '\n'.join(f'例 {index}　' + GENERATORS[kind](rng) for index in range(1, count + 1))
