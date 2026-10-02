from decimal import Decimal, ROUND_HALF_UP


# 这是一个用于测试的score_dict值，score_dict是一个从别处传来的字典，里面包含did_point命中数，all_point总数，total_score满分，lose_score扣分
#score_dict = {"did_point": 30, "all_point": 33, "total_score": 15, "lose_score":1}



def get_score(score_dict) -> Decimal:
    did_point = Decimal(str(score_dict["did_point"]))
    all_point = Decimal(str(score_dict["all_point"]))
    total_score = Decimal(str(score_dict["total_score"]))
    lose_score = Decimal(str(score_dict["lose_score"]))

    if all_point == 0:
        raw = Decimal("0")
    elif did_point > all_point:
        raw = total_score 
    else:
        raw = did_point/all_point * total_score

    
    final_score = raw - lose_score
    if final_score < 0:
        final_score = Decimal("0.0")
    final_score = final_score.quantize(Decimal("0.1"),rounding=ROUND_HALF_UP)

    return final_score



