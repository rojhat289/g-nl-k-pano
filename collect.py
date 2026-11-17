"""Günlük Pano - veri toplayıcı.
Her kaynak ayrı bir fonksiyondur. Biri bozulursa diğerleri çalışmaya devam eder.
Yeni kaynak eklemek için: bir fonksiyon yaz, en alttaki KAYNAKLAR listesine ekle."""
import csv, html, json, re, urllib.parse, urllib.request, urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SIMDI = datetime.now(ZoneInfo("Europe/Istanbul"))
VERI = Path("data")
LATEST, ARSIV = VERI / "latest.json", VERI / "archive.csv"
UA = {"User-Agent": "gunluk-pano/1.0 (kisisel proje, gunde bir kez)"}

ILLER = {  # il: (enlem, boylam)
    "İstanbul": (41.01, 28.98), "Ankara": (39.93, 32.86), "İzmir": (38.42, 27.14),
    "Bursa": (40.19, 29.06), "Antalya": (36.90, 30.69), "Gaziantep": (37.07, 37.38),
    "Mersin": (36.80, 34.64), "Adana": (37.00, 35.32),
}
HAVA_KODU = {0: "Açık", 1: "Az bulutlu", 2: "Parçalı bulutlu", 3: "Kapalı", 45: "Sisli",
             48: "Sisli", 51: "Çisenti", 53: "Çisenti", 55: "Çisenti", 61: "Hafif yağmur",
             63: "Yağmurlu", 65: "Kuvvetli yağmur", 71: "Hafif kar", 73: "Karlı",
             75: "Yoğun kar", 80: "Sağanak", 81: "Sağanak", 82: "Şiddetli sağanak",
             95: "Gök gürültülü", 96: "Dolu ihtimali", 99: "Dolu ihtimali"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def robots_izin(url):
    """Siteye istek atmadan önce robots.txt'e bakar; yasaklıysa False döner."""
    p = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(get(f"{p.scheme}://{p.netloc}/robots.txt").decode("utf-8", "replace").splitlines())
    except Exception:
        return True  # robots.txt okunamadıysa kısıtlama yok sayılır
    return rp.can_fetch(UA["User-Agent"], url)


def kurlar():
    """TCMB günlük XML'i. Efektif satış = BanknoteSelling alanı."""
    kok = ET.fromstring(get("https://www.tcmb.gov.tr/kurlar/today.xml"))
    cikti = {"tarih": kok.get("Tarih"), "bulten": kok.get("Bulten_No")}
    for kod in ("USD", "EUR"):
        c = kok.find(f"Currency[@Kod='{kod}']")
        cikti[kod] = {"efektif_alis": float(c.findtext("BanknoteBuying")),
                      "efektif_satis": float(c.findtext("BanknoteSelling"))}
    return cikti


def hava():
    """Open-Meteo (anahtarsız, CC BY 4.0). Bugünün tahmini, her il için bir istek."""
    cikti = {}
    for il, (enlem, boylam) in ILLER.items():
        sorgu = urllib.parse.urlencode({
            "latitude": enlem, "longitude": boylam, "forecast_days": 1,
            "timezone": "Europe/Istanbul",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
        g = json.loads(get("https://api.open-meteo.com/v1/forecast?" + sorgu))["daily"]
        kod = g["weather_code"][0]
        cikti[il] = {"durum": HAVA_KODU.get(kod, f"Hava kodu {kod}"), "max": g["temperature_2m_max"][0],
                     "min": g["temperature_2m_min"][0], "yagis": g["precipitation_probability_max"][0]}
    return cikti


def resmi_gazete():
    """Günün fihristindeki madde bağlantılarını toplar (/eskiler/YYYY/AA/YYYYAAGG-N.htm|pdf)."""
    gun = SIMDI.strftime("%Y%m%d")
    fihrist = f"https://www.resmigazete.gov.tr/fihrist?tarih={SIMDI:%Y-%m-%d}"
    cikti = {"fihrist": fihrist, "maddeler": [],
             "pdf": f"https://www.resmigazete.gov.tr/eskiler/{SIMDI:%Y/%m}/{gun}.pdf"}
    if not robots_izin(fihrist):
        cikti["not"] = "Site otomatik okumaya izin vermiyor; yalnızca bağlantılar gösteriliyor."
        return cikti
    sayfa = get(fihrist).decode("utf-8", "replace")
    desen = rf'<a[^>]+href="([^"]*eskiler/\d{{4}}/\d{{2}}/{gun}-\d+\.(?:htm|pdf))"[^>]*>(.*?)</a>'
    gorulen = set()
    for href, ic in re.findall(desen, sayfa, re.S):
        baslik = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", ic)).split())
        url = urllib.parse.urljoin(fihrist, href)
        if baslik and url not in gorulen:
            gorulen.add(url)
            cikti["maddeler"].append({"baslik": baslik, "url": url})
    if not cikti["maddeler"]:
        cikti["not"] = "Fihristte madde bağlantısı bulunamadı; sayfa farklı yapıda olabilir."
    return cikti


def arsivle(taze):
    """Bugünün sayılarını archive.csv'ye yazar (aynı gün tekrar çalışırsa satırı günceller)."""
    satir = {"tarih": SIMDI.strftime("%Y-%m-%d")}
    if "kurlar" in taze:
        for kod in ("USD", "EUR"):
            satir[f"{kod}_efektif_satis"] = taze["kurlar"][kod]["efektif_satis"]
    if "hava" in taze:
        for il, h in taze["hava"].items():
            satir[f"{il}_min"], satir[f"{il}_max"] = h["min"], h["max"]
    if "resmi_gazete" in taze:
        satir["resmi_gazete_madde"] = len(taze["resmi_gazete"]["maddeler"])
    satirlar = {}
    if ARSIV.exists():
        with ARSIV.open(encoding="utf-8-sig") as f:
            satirlar = {r["tarih"]: r for r in csv.DictReader(f)}
    satirlar[satir["tarih"]] = {**satirlar.get(satir["tarih"], {}), **satir}  # aynı günün eski değerleri korunur
    alanlar = list(dict.fromkeys(a for r in satirlar.values() for a in r))
    with ARSIV.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel Türkçe karakterleri doğru açar
        w = csv.DictWriter(f, fieldnames=alanlar, restval="")
        w.writeheader()
        w.writerows(satirlar.values())


KAYNAKLAR = {"kurlar": kurlar, "hava": hava, "resmi_gazete": resmi_gazete}


def main():
    VERI.mkdir(exist_ok=True)
    onceki = json.loads(LATEST.read_text(encoding="utf-8")) if LATEST.exists() else {}
    taze, hatalar = {}, {}
    for ad, fonksiyon in KAYNAKLAR.items():
        try:
            taze[ad] = fonksiyon()
        except Exception as e:  # bir kaynak bozulsa da diğerleri devam eder
            hatalar[ad] = f"{type(e).__name__}: {e}"
            print("HATA:", ad, hatalar[ad])
    veri = {ad: taze.get(ad) or onceki.get(ad) for ad in KAYNAKLAR}  # hata varsa son başarılı veri kalır
    veri["guncelleme"] = SIMDI.isoformat(timespec="minutes")
    veri["hatalar"] = hatalar
    LATEST.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
    arsivle(taze)
    print("Bitti. Başarılı:", list(taze), "| Hatalı:", list(hatalar))


if __name__ == "__main__":
    main()
