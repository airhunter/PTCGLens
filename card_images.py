"""后台取得规则一致、插画一致的简中卡面，或同一英文印次的高清图。"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np

from viewer import large_card_bytes
from card_translation import readable_text


DATA_ROOT = "https://raw.githubusercontent.com/duanxr/PTCG-CHS-Datasets/main/"
CATALOG_URL = DATA_ROOT+"ptcg_chs_infos.json"
MAPPING_VERSION = 3
ENERGY_CODES = {"1":"G","2":"R","3":"W","4":"L","5":"P","6":"F",
                "7":"D","8":"M","9":"Y","11":"C"}
ENGLISH_ENERGY = {"Grass":"G","Fire":"R","Water":"W","Lightning":"L","Psychic":"P",
                  "Fighting":"F","Darkness":"D","Metal":"M","Fairy":"Y","Colorless":"C"}


def normal(text):
    value = html.unescape(re.sub(r"<[^>]*>","",str(text or "")))
    value = unicodedata.normalize("NFKD",value).casefold().replace("×","x")
    return "".join(char for char in value if char.isalnum())


def rule_normal(text):
    """规则比较保留能量图标、数字及运算符，避免 30+ 和 30 被当成同一效果。"""
    value = unicodedata.normalize("NFKD", readable_text(str(text or ""))).casefold().replace("×", "x")
    return "".join(char for char in value if char.isalnum() or char in "+-=*/<>")


def effect_text(text):
    text = str(text or "").split("|",1)[0]
    return re.sub(r"在自己的回合可以使用任意张物品卡。?\s*$","",text).strip()


def digest(text):
    return hashlib.sha256(normal(text).encode()).hexdigest()


def chinese_printing_rank(candidate, regulation_mark):
    details = candidate["details"]
    promo = ("PROMO" in str(candidate.get("commodityCode","")).upper()
             or not re.fullmatch(r"\d+/\d+",str(details.get("collectionNumber",""))))
    return (promo, details.get("rarityText") not in ("C","U","R"),
            details.get("regulationMarkText") != regulation_mark)


def chinese_moves(details):
    return [{"kind":"ability","name":move.get("featureName"),"text":move.get("featureDesc"),
             "damage":"","cost":""} for move in details.get("cardFeatureItemList",[])]+[
        {"kind":"attack","name":move.get("abilityName"),
         "text":"" if move.get("abilityText") in (None,"none") else move["abilityText"],
         "damage":move.get("abilityDamage"),
         "cost":"".join(ENERGY_CODES.get(code,"?") for code in str(move.get("abilityCost") or "").split(",") if code)}
        for move in details.get("abilityItemList",[])]


def chinese_rules_match(card, candidate, bridges):
    """不接受只有名称或 HP 相同；规则缺少可靠对照时保留英文。"""
    details = candidate.get("details",{})
    if normal(card.get("name_zh")) != normal(candidate.get("name")) or not card.get("name_zh"):
        return False
    if int(card.get("hp") or 0) != int(details.get("hp") or 0):
        return False
    if not card.get("hp"):
        if candidate.get("cardType") != "2" or card.get("attacks"):
            return False
        chinese = effect_text(details.get("ruleText"))
        if card.get("card_text_zh"):
            if rule_normal(card["card_text_zh"]) == rule_normal(chinese):
                return True
            if card.get("card_text_zh_source") != "local":
                return False
            # 本地参考译文与简中卡面措辞不同时，仍可使用已逐条核对的规则桥。
        bridge = bridges.get(card.get("name_en"),{})
        return (digest(card.get("card_text_en")) in bridge.get("english",[])
                and digest(chinese) in bridge.get("chinese",[]))
    if candidate.get("cardType") != "1":
        return False
    moves = chinese_moves(details)
    if len(moves) != len(card.get("attacks",[])):
        return False
    for move,attack in zip(moves,card["attacks"]):
        if (move["kind"] != attack["kind"] or not attack.get("name_zh")
                or normal(move["name"]) != normal(attack["name_zh"])
                or rule_normal(move["damage"]) != rule_normal(attack.get("damage"))):
            return False
        if attack["kind"] == "attack" and ("cost" not in attack or sorted(move["cost"]) != sorted(attack["cost"])):
            return False
        if attack.get("text_en") and not attack.get("text_zh"):
            return False
        if rule_normal(move["text"]) != rule_normal(attack.get("text_zh")):
            return False
    return True


def tcgdex_ids(card_id):
    match = re.fullmatch(r"(sv|sm|xy|bw|swsh|me)(\d+)(?:-(\d))?_en_(\d{3})",card_id)
    if not match:
        return []
    family,series,part,number = match.groups()
    set_id = family+(str(int(series)) if family=="swsh" else f"{int(series):02}")
    if part:
        set_id += "."+part
    return [set_id+"-"+number,set_id+"-"+str(int(number))]


def english_rules_match(card, remote):
    if (normal(card["name_en"]) != normal(remote.get("name"))
            or str(card["number"]).lstrip("0") != str(remote.get("localId","")).lstrip("0")
            or int(card.get("hp") or 0) != int(remote.get("hp") or 0)):
        return False
    if not card.get("hp"):
        return bool(card.get("card_text_en")) and rule_normal(card["card_text_en"]) == rule_normal(remote.get("effect"))
    moves = [("ability",m) for m in remote.get("abilities",[])]+[("attack",m) for m in remote.get("attacks",[])]
    if len(moves) != len(card.get("attacks",[])):
        return False
    for (kind,move),attack in zip(moves,card["attacks"]):
        if (kind != attack["kind"] or normal(move.get("name")) != normal(attack["name_en"])
                or rule_normal(move.get("effect")) != rule_normal(attack.get("text_en"))
                or rule_normal(move.get("damage")) != rule_normal(attack.get("damage"))):
            return False
        if kind=="attack" and "cost" in attack:
            cost = [ENGLISH_ENERGY.get(energy,"?") for energy in move.get("cost",[])]
            if sorted(cost) != sorted(attack["cost"]):
                return False
    return True


def same_art(reference, candidate, pokemon=False):
    """只比较插画，不用跨语言牌面上的文字确认图案。"""
    def art(image):
        image = cv2.resize(image,(320,448),interpolation=cv2.INTER_AREA)
        start,end = (.07,.48) if pokemon else (.12,.52)
        return cv2.cvtColor(image[round(448*start):round(448*end),16:304],cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=600)
    source,sd = sift.detectAndCompute(art(reference),None)
    target,td = sift.detectAndCompute(art(candidate),None)
    if sd is None or td is None:
        return False
    pairs = cv2.BFMatcher().knnMatch(sd,td,k=2)
    matches = [p[0] for p in pairs if len(p)==2 and p[0].distance<.72*p[1].distance]
    if len(matches)<12:
        return False
    src = np.float32([source[m.queryIdx].pt for m in matches]).reshape(-1,1,2)
    dst = np.float32([target[m.trainIdx].pt for m in matches]).reshape(-1,1,2)
    matrix,mask = cv2.findHomography(src,dst,cv2.RANSAC,3.)
    if matrix is None or mask is None or int(mask.sum())<12 or float(mask.mean())<.6:
        return False
    valid = src[mask.ravel().astype(bool)].reshape(-1,2)
    return bool(np.ptp(valid[:,0])>72 and np.ptp(valid[:,1])>25)


class CardImageSources:
    def __init__(self, root, cache_root, visual_dir, large_dir):
        self.root,self.cache_root,self.visual_dir,self.large_dir = map(Path,(root,cache_root,visual_dir,large_dir))
        self.root.mkdir(parents=True,exist_ok=True)
        self.catalog = None
        self.preference = "zh"
        self.retry_after = {}
        self.cancelled = lambda:False
        self.deadline = float("inf")
        self.bridges = json.loads((Path(__file__).parent / "card-image-rules.json").read_text(encoding="utf-8"))

    @staticmethod
    def download(url, limit=8*1024*1024):
        allowed = {"raw.githubusercontent.com","api.tcgdex.net","assets.tcgdex.net"}
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in allowed:
            raise ValueError("卡图来源地址无效")
        request = urllib.request.Request(url,headers={"User-Agent":"PTCGLens/1.0"})
        with urllib.request.urlopen(request,timeout=8) as response:
            if urlsplit(response.geturl()).hostname not in allowed:
                raise ValueError("卡图来源重定向无效")
            body = response.read(limit+1)
            if len(body)>limit:
                raise ValueError("卡图来源文件过大")
            return body

    def json_resource(self, path, url, limit=1024*1024):
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cached,dict):
                    return cached
            except (OSError,ValueError):
                pass
        if self.cancelled() or time.monotonic()>self.deadline:
            raise OSError("卡图请求已取消")
        body = self.download(url,limit)
        result = json.loads(body)
        if not isinstance(result,dict):
            raise ValueError("卡牌数据格式无效")
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(path)
        return result

    def image_resource(self, image_path, url):
        if image_path.is_file():
            body = image_path.read_bytes()
        else:
            if self.cancelled() or time.monotonic()>self.deadline:
                raise OSError("卡图请求已取消")
            body = self.download(url)
        image = cv2.imdecode(np.frombuffer(body,np.uint8),cv2.IMREAD_COLOR)
        if image is None or image.shape[1]<300 or image.shape[0]<400 or image.shape[0]>4000 or image.shape[1]>4000:
            raise ValueError("卡图不是可用的高清图片")
        if not image_path.is_file():
            temporary = image_path.with_suffix(".tmp")
            temporary.write_bytes(body)
            temporary.replace(image_path)
        return body,image

    def load_catalog(self):
        if self.catalog is None:
            data = self.json_resource(self.root / "ptcg_chs_infos.json",CATALOG_URL,40*1024*1024)
            if not isinstance(data.get("collections"),list):
                raise ValueError("简中卡牌数据格式无效")
            self.catalog = {}
            for collection in data["collections"]:
                for card in collection.get("cards",[]):
                    if not isinstance(card.get("details"),dict):
                        continue
                    self.catalog.setdefault(normal(card.get("name")),[]).append(card)
        return self.catalog

    def chinese(self, card, reference):
        if not card.get("name_zh"):
            return None
        matches = [c for c in self.load_catalog().get(normal(card.get("name_zh")),[])
                   if chinese_rules_match(card,c,self.bridges)]
        # 普通印次优先；规则一致后仍核对插画，金卡/异画不静默替换。
        matches.sort(key=lambda c:chinese_printing_rank(c,card.get("regulation_mark")))
        seen = set()
        for candidate in matches[:8]:
            if self.cancelled() or time.monotonic()>self.deadline:
                break
            path = candidate.get("image","")
            if not re.fullmatch(r"img/\d+/\d+\.png",path) or path in seen:
                continue
            seen.add(path)
            try:
                body,image = self.image_resource(self.root / ("zh-"+str(candidate["id"])+".png"),DATA_ROOT+path)
                if same_art(reference,image,bool(card.get("hp"))):
                    return {"bytes":body,"language":"简中","provider":"PTCG-CHS-Datasets",
                        "source_id":str(candidate["id"]),"source_url":DATA_ROOT+path,
                        "printing":candidate["details"].get("collectionNumber"),
                        "set":candidate.get("commodityCode"),
                        "chinese_effect":effect_text(candidate["details"].get("ruleText")) if not card.get("hp") else None}
            except (OSError,ValueError):
                continue
        return None

    def english(self, card, reference):
        for remote_id in dict.fromkeys(tcgdex_ids(card["card_id"])):
            if self.cancelled() or time.monotonic()>self.deadline:
                break
            try:
                remote = self.json_resource(self.root / (remote_id+".json"),"https://api.tcgdex.net/v2/en/cards/"+remote_id)
                if (remote.get("id") != remote_id or remote.get("set",{}).get("id") != remote_id.rsplit("-",1)[0]
                        or not english_rules_match(card,remote)):
                    continue
                url = str(remote.get("image",""))+"/high.png"
                body,image = self.image_resource(self.root / ("en-"+remote_id+".png"),url)
                if same_art(reference,image,bool(card.get("hp"))):
                    return {"bytes":body,"language":"英文","provider":"TCGdex","source_id":remote_id,
                            "source_url":url,"printing":remote["localId"],"set":remote["set"]["id"]}
            except (OSError,ValueError):
                continue
        return None

    def resolve(self, card, cancelled=lambda:False):
        self.cancelled = cancelled
        self.deadline = time.monotonic()+10
        card_id = card["card_id"]
        preference = self.preference
        if not re.fullmatch(r"[a-z0-9-]+_en_\d{3}",card_id):
            raise ValueError("卡牌编号无效")
        def local():
            return {"bytes":large_card_bytes(card_id,self.cache_root,self.large_dir,self.visual_dir),
                    "language":"英文","provider":"游戏缓存","source_id":card_id}
        if preference == "local" or cancelled():
            return local()
        # 缓存绑定完整规则签名；索引规则改变时不复用此前的对应关系。
        signature = hashlib.sha256(json.dumps(
            [MAPPING_VERSION,card,self.bridges],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        mapping_path = self.root / (card_id+".mapping.json")
        if mapping_path.is_file():
            try:
                mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                if (mapping["signature"] == signature and mapping.get("preference") == preference
                        and mapping.get("file") == card_id+".resolved.png"):
                    body = (self.root / mapping["file"]).read_bytes()
                    image = cv2.imdecode(np.frombuffer(body,np.uint8),cv2.IMREAD_COLOR)
                    if image is not None:
                        return {**mapping,"bytes":body}
            except (OSError,ValueError,KeyError):
                pass
        retry_key = (card_id,preference)
        if time.monotonic() >= self.retry_after.get(retry_key,0):
            self.retry_after[retry_key] = time.monotonic()+300
            reference = cv2.imdecode(np.frombuffer((self.visual_dir / (card_id+".png")).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
            if reference is not None:
                result = None
                providers = (self.chinese,self.english) if preference=="zh" else (self.english,self.chinese)
                for provider in providers:
                    try:
                        result = provider(card,reference)
                    except (OSError,ValueError,KeyError):
                        continue
                    if result:
                        break
                if cancelled():
                    self.retry_after.pop(retry_key,None)
                    return local()
                if result:
                    destination = self.root / (card_id+".resolved.png")
                    destination.write_bytes(result["bytes"])
                    mapping = {key:value for key,value in result.items() if key!="bytes"}
                    mapping.update(signature=signature,file=destination.name,preference=preference)
                    mapping_path.write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding="utf-8")
                    return result
        return local()
