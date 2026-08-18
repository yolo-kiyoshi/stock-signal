from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InitialInstrument:
    """初期ウォッチリストへ登録する日本株。"""

    symbol: str
    display_name: str
    industry: str


JQUANTS_INITIAL_INSTRUMENTS = (
    InitialInstrument("7203", "トヨタ自動車", "輸送用機器"),
    InitialInstrument("6758", "ソニーグループ", "電気機器"),
    InitialInstrument("8306", "三菱UFJフィナンシャル・グループ", "銀行業"),
    InitialInstrument("4502", "武田薬品工業", "医薬品"),
    InitialInstrument("9432", "日本電信電話", "情報・通信業"),
    InitialInstrument("8058", "三菱商事", "卸売業"),
    InitialInstrument("9983", "ファーストリテイリング", "小売業"),
    InitialInstrument("9503", "関西電力", "電気・ガス業"),
    InitialInstrument("9020", "東日本旅客鉄道", "陸運業"),
    InitialInstrument("1925", "大和ハウス工業", "建設業"),
)
