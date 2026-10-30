"""Synthetic 30-day conversation log with ground-truth memory labels.

Three groups, 50 facts total:

    remember  15  should still be recalled verbatim/summarised on day 30
    fog       15  should be recallable but degraded (gist / impression)
    forget    20  one-off trivia that must NOT surface on day 30

The data is hand-written rather than templated on purpose: templated facts
share too many tokens and inflate keyword recall for free.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, NamedTuple, Tuple

REMEMBER: Tuple[Tuple[str, str], ...] = (
    ("数学考砸", "我数学考砸了，哭了好久，觉得自己特别没用"),
    ("爸妈离婚", "我爸妈上个月离婚了，我不知道该跟谁"),
    ("拿到offer", "我拿到那家公司的offer了！！开心到睡不着"),
    ("狗走了", "我养了十年的狗今天走了，我到现在还不敢回家"),
    ("焦虑症", "我确诊焦虑症了，医生让我开始吃药"),
    ("高考", "高考完那天下好大的雨，我一个人在操场上站了很久"),
    ("喜欢男生", "我一直不敢告诉别人，其实我喜欢男生"),
    ("奶奶住院", "奶奶住院了，我每天下班都要去医院，我很怕"),
    ("约定", "我们说好了，每年今天都要聊一次，你别忘了"),
    ("生日", "你记得我生日是三月十七吗？你上次答应我要记住的"),
    ("加班到凌晨", "第一次见你的时候，我加班到凌晨三点，特别崩溃"),
    ("裁员", "我被裁员了，存款只够撑三个月，不敢跟家里说"),
    ("抑郁症复发", "抑郁症复发那阵子，真的只有你在陪我"),
    ("搬去成都", "我搬去成都了，这边一直下雨，很潮湿"),
    ("学画画", "我说过我想学画画，但拖了两年都没开始"),
)

FOG: Tuple[Tuple[str, str], ...] = (
    ("吉他", "我最近在学吉他，手指头疼"),
    ("新手机", "我换了个新手机，还在适应"),
    ("楼下猫", "楼下那只猫好像生小猫了"),
    ("杭州", "周末去了趟杭州，西湖人特别多"),
    ("跑步", "我开始跑步了，每天三公里"),
    ("团建", "同事叫我去团建，我不太想去"),
    ("键盘", "我买了个新键盘，声音很好听"),
    ("美剧", "最近在追一部美剧，节奏有点慢"),
    ("相亲", "我妈又让我去相亲了"),
    ("失眠", "这几天老是失眠，躺下就清醒"),
    ("换了发型", "我换了发型，剪短了"),
    ("做菜", "最近在学做菜，昨天炒糊了"),
    ("查体", "去做了个体检，等结果"),
    ("房间", "把房间重新布置了一下，换了张桌子"),
    ("伴郎", "朋友结婚，我去当伴郎了"),
)

FORGET: Tuple[str, ...] = (
    "今天吃了黄焖鸡",
    "外面在下雨",
    "地铁好挤啊",
    "我手机没电了",
    "刚点了杯奶茶",
    "今天好热",
    "隔壁在装修，吵死了",
    "我打了个哈欠",
    "快递到了",
    "楼下便利店关门了",
    "我换了张壁纸",
    "今天星期四",
    "我在等外卖",
    "空调有点冷",
    "我刚洗完澡",
    "今天的云挺好看的",
    "我找不到充电线了",
    "闹钟没响",
    "咖啡凉了",
    "袜子湿了",
)

FILLER: Tuple[str, ...] = (
    "在吗",
    "今天好累啊",
    "嗯嗯",
    "我先去吃饭了",
    "你呢",
    "刚下班",
    "有点困",
    "哈哈哈",
    "好吧",
    "在干嘛",
)


class Fact(NamedTuple):
    key: str
    text: str
    group: str


class Turn(NamedTuple):
    at: datetime
    text: str
    fact_key: str  # empty for filler


def build(days: int = 30, seed: int = 7) -> Tuple[List[Turn], List[Fact]]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)
    turns: List[Turn] = []
    facts: List[Fact] = []

    # Spread mentions across the month. Remembered facts repeat early and often;
    # fogged facts appear twice; forgotten facts appear exactly once.
    remember_days: Dict[int, List[int]] = {}
    for idx in range(len(REMEMBER)):
        span = [1 + (idx * 2 + k * 5) % 26 for k in range(5)]
        remember_days[idx] = sorted(set(span))

    fog_days: Dict[int, List[int]] = {}
    for idx in range(len(FOG)):
        fog_days[idx] = sorted({2 + (idx * 7) % 20, 6 + (idx * 7) % 22})

    forget_day: Dict[int, int] = {
        idx: 1 + (idx * 3) % 28 for idx in range(len(FORGET))
    }

    for day in range(1, days + 1):
        day_start = start + timedelta(days=day - 1)

        for idx, key, text in _indexed(REMEMBER):
            if day in remember_days[idx]:
                turns.append(Turn(day_start + timedelta(minutes=idx % 30), text, key))

        for idx, key, text in _indexed(FOG):
            if day in fog_days[idx]:
                turns.append(Turn(day_start + timedelta(minutes=40 + idx % 20), text, key))

        for idx, text in enumerate(FORGET):
            if forget_day[idx] == day:
                turns.append(Turn(day_start + timedelta(minutes=60 + idx % 20), text, text))

        for j in range(rng.randint(3, 7)):
            turns.append(
                Turn(
                    day_start + timedelta(minutes=80 + j * 7, seconds=rng.randint(0, 59)),
                    rng.choice(FILLER),
                    "",
                )
            )

    turns.sort(key=lambda t: t.at)
    facts.extend(Fact(k, t, "remember") for k, t in REMEMBER)
    facts.extend(Fact(k, t, "fog") for k, t in FOG)
    facts.extend(Fact(t, t, "forget") for t in FORGET)
    return turns, facts


def queries(facts: List[Fact], days: int = 30, seed: int = 11) -> Dict[str, List[Tuple[str, Fact]]]:
    """Return probe queries per group.

    `generic` probes are used to measure over-recall: on a neutral question the
    system must not volunteer forgotten trivia.
    """
    rng = random.Random(seed)
    del days
    generic_pool = [
        "今天过得怎么样",
        "在干嘛呢",
        "随便聊聊吧",
        "最近好吗",
        "我有点无聊",
        "陪我说说话",
    ]
    out: Dict[str, List[Tuple[str, Fact]]] = {"remember": [], "fog": [], "forget": []}
    for fact in facts:
        if fact.group == "remember":
            out["remember"].append(("你还记得{}吗".format(fact.key), fact))
        elif fact.group == "fog":
            out["fog"].append(("之前是不是提过{}".format(fact.key), fact))
        else:
            out["forget"].append((rng.choice(generic_pool), fact))
    return out


def _indexed(items):
    for idx, (key, text) in enumerate(items):
        yield idx, key, text
