import pytest
from decimal import Decimal
from scoring import get_score

def case(**kw):
    base = {"did_point": 0, "all_point": 4, "total_score": 15, "lose_score":0}
    base.update(kw)
    return base

CASE = [
    # 说明，             输入，                  期望
    ("正常计分",        case(did_point=30, all_point=33, lose_score=1), "12.6"),
    ("除零",            case(all_point=0),                             "0.0"),
    ("除零且仍有扣分",   case(all_point=0, lose_score=3),               "0.0"),
    ("命中越界封顶",     case(did_point=99, lose_score=3),               "12.0"),
    ("越界时扣分仍生效", case(did_point=99, lose_score=20),              "0.0"),
    ("扣成负夹到零",     case(lose_score=5),                             "0.0"),
    ("扣分带小数",       case(did_point=99, lose_score=1.25),             "13.8"),
    ("全命中",          case(did_point=4),                              "15.0"),
]

@pytest.mark.parametrize("说明,输入,期望", CASE , ids=[c[0] for c in CASE])
def test_get_scoring(说明, 输入, 期望):
    assert get_score(输入) == Decimal(期望)

def test_returnDecimal():
    assert isinstance(get_score(case(did_point=4)),Decimal)








