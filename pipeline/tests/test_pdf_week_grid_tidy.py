"""ФМО: дочистка записей — даты, время, мусор PDF, языковые подгруппы, преподаватели (19.11, перед показом БГУ)."""
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.pdf_week_grid import fix_teachers, tidy_entry  # noqa: E402


def T(title, teachers=()):
    return tidy_entry((title, list(teachers), ["1"], [], None, None, None, None, [], ""))


def test_dates_and_time_in_title():
    r = T("20.11 – МЦП: Защита персональных данных")
    assert r[0] == "МЦП: Защита персональных данных" and r[7] == [(20, 11)]
    assert T("27.11; 11.12 – Основы управления")[7] == [(27, 11), (11, 12)]
    assert T("3/12 International Trade and Incoterms")[7] == [(3, 12)]
    r = T("12.50 – С 7.10 – Политическое страноведение")
    assert (r[0], r[4], r[6]) == ("Политическое страноведение", (7, 10), (time(12, 50), None))
    r = T("11.12 - Дипломатическая служба – ЗАЧЕТ")
    assert r[0] == "Дипломатическая служба" and "зачёт" in r[3]


def test_garbage_and_fragments():
    assert T("Н Исе пм . . Д Ч ве ор рн кия нв ас к Еи .Юй А . –. А 80./ 3Ш вайба О.Г.")[0].startswith("Занятие")
    r = T("Правовое обеспеhчtеtнpsи:е// рeаdзuвfiиr.тbиsяu .эbлyе/cкoтuрrоsнe/нvоieгоw .гpоhсpу?дiаdр=с3т6в8а7")
    assert r[0] == "Правовое обеспечение развития электронного государства" and r[9].startswith("https://")
    assert T("urce/content/1/Tema_5_Cartas_protocolarias.pdf) А.А.") is None
    assert T("споров") is None
    r = T("ауд.703) Англ.6")
    assert r[0] == "Иностранный язык" and r[3] == ["Англ.6"]


def test_teachers():
    assert fix_teachers(["Пуньк о Т.Н.", "В 8 .0 С2 .", "(пр.)2 Кузьмина-Мамедова", "Чернявский АА.", "Радько А.А/Сюе Юйбо",
                         "И.В. Жилинская", "ШиловаМ.В.", "А З.АВЧ. –Е 8Т1 0- Сачава П.Д."]) == [
        "Пунько Т.Н.", "Кузьмина-Мамедова", "Чернявский А.А.", "Радько А.А.", "Сюе Юйбо", "Жилинская И.В.", "Шилова М.В.", "Сачава П.Д."]
