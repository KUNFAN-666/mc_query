# -*- coding: utf-8 -*-
"""AstrBot MC 服务器查询插件

指令:
  /mcadd <名称> <域名> [介绍]    添加服务器(白名单在插件设置里改)
  /mc [域名]                     查询服务器, 显示 logo/在线/ping/版本/MOTD 并渲染图片
"""

from __future__ import annotations
import os
import json
import re
import base64
import io
import asyncio

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from PIL import Image, ImageDraw, ImageFont

# ============ 存储 ============
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_PLUGIN_DIR, "..", ".."))  # AstrBot data 目录
_DATA_FILE = os.path.join(_DATA_DIR, "mc_query", "data.json")


def ensure_store() -> dict:
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"servers": {}, "session_default": {}}


def save_store(store: dict):
    os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
    tmp = _DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DATA_FILE)


def session_of(event: AstrMessageEvent) -> str:
    obj = getattr(event, "message_obj", None)
    sid = getattr(obj, "session_id", "") or ""
    if not sid:
        sid = str(event.get_sender_id())
    return sid


# ============ 图片渲染 ============
def _load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(text: str, per_line: int):
    text = text or ""
    return [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]


def _load_bg(path, W, H):
    """加载 book 作为背景, cover 填充到 WxH。保留透明(RGBA)。若已是目标尺寸则直接返回。"""
    try:
        bg = Image.open(path).convert("RGBA")
        if bg.size == (W, H):
            return bg
        bw, bh = bg.size
        scale = max(W / bw, H / bh)
        nw, nh = int(bw * scale), int(bh * scale)
        bg = bg.resize((nw, nh), Image.LANCZOS)
        left = (nw - W) // 2
        top = (nh - H) // 2
        return bg.crop((left, top, left + W, top + H))
    except Exception:
        return Image.new("RGB", (W, H), (245, 235, 215))


# MC 颜色码 → RGB
_COLOR_MAP = {
    "0": (0, 0, 0), "1": (0, 0, 170), "2": (0, 170, 0), "3": (0, 170, 170),
    "4": (170, 0, 0), "5": (170, 0, 170), "6": (255, 170, 0), "7": (170, 170, 170),
    "8": (85, 85, 85), "9": (85, 85, 255), "a": (85, 255, 85), "b": (85, 255, 255),
    "c": (255, 85, 85), "d": (255, 85, 255), "e": (255, 255, 85), "f": (255, 255, 255),
}


def _mc_segments(motd: str, default):
    """把带 § 颜色码的 MOTD 解析为 [(text, rgb)] 分段。"""
    segs = []
    cur = ""
    color = default
    i = 0
    while i < len(motd):
        ch = motd[i]
        if ch == "§" and i + 1 < len(motd):
            code = motd[i + 1].lower()
            if code in ("k", "l", "m", "n", "o"):  # 格式码, 忽略
                i += 2
                continue
            if code in _COLOR_MAP:
                if cur:
                    segs.append((cur, color))
                color = _COLOR_MAP[code]
                cur = ""
                i += 2
                continue
            if code == "r":
                if cur:
                    segs.append((cur, color))
                color = default
                cur = ""
                i += 2
                continue
            if code == "x" and i + 8 <= len(motd):  # §xRRGGBB hex(共8字符)
                try:
                    rgb = tuple(int(motd[i + 2 + j:i + 4 + j], 16) for j in range(0, 6, 2))
                    if cur:
                        segs.append((cur, color))
                    color = rgb
                    cur = ""
                    i += 8
                    continue
                except Exception:
                    pass
            i += 1
            continue
        cur += ch
        i += 1
    if cur:
        segs.append((cur, color))
    return segs


def _draw_mc_center(d, cx, y, text, font, default):
    """居中绘制带 § 颜色码的 MOTD, 支持多行(\\n)。"""
    lh = font.size if hasattr(font, "size") else 30
    for idx, line in enumerate(text.split("\n")):
        segs = _mc_segments(line, default)
        total = sum(d.textlength(t, font=font) for t, _ in segs)
        x = cx - total / 2
        for t, c in segs:
            d.text((x, y + idx * (lh + 8)), t, font=font, fill=c)
            x += d.textlength(t, font=font)


def _draw_center_colored(d, cx, vcenter_y, text, font, default):
    """在 vcenter_y 处垂直居中绘制带 § 颜色码的文本(单行, 自动处理 §x 变色)。"""
    lh = font.size if hasattr(font, "size") else 30
    _draw_mc_center(d, cx, vcenter_y - lh / 2, text, font, default)


def _truncate_vis(text, max_vis):
    """按可见字符数截断, 保留 § 颜色码(避免切断 §xRRGGBB)。"""
    if not text:
        return ""
    out = ""
    vis = 0
    i, n = 0, len(text)
    while i < n and vis < max_vis:
        ch = text[i]
        if ch == "§" and i + 1 < n:
            code = text[i + 1]
            if code.lower() == "x" and i + 8 <= n:
                out += text[i:i + 8]
                i += 8
                continue
            out += text[i:i + 2]
            i += 2
            continue
        out += ch
        vis += 1
        i += 1
    return out


def _last_color_prefix(s):
    """取字符串中最后一个 § 颜色码(§c 或 §xRRGGBB), 用于换行后延续颜色。"""
    codes = re.findall(r"§x[0-9a-fA-F]{6}|§[0-9a-fk-or]", s or "")
    return codes[-1] if codes else ""


def _wrap_mc(text, font, max_w):
    """按可见宽度换行(保留 § 码, 换行延续颜色, 尽量在空格处断行), 返回 \\n 连接的字符串。"""
    if not text:
        return ""
    out_lines = []
    for raw in (text or "").split("\n"):
        cur = ""          # 当前行原始内容(含 §)
        cur_vis = ""      # 当前行可见字符
        last_space = -1   # cur 中上一个空格的索引
        j = 0
        n = len(raw)
        while j < n:
            ch = raw[j]
            if ch == "§" and j + 1 < n:
                code = raw[j + 1]
                if code.lower() == "x" and j + 8 <= n:
                    cur += raw[j:j + 8]
                    j += 8
                    continue
                cur += raw[j:j + 2]
                j += 2
                continue
            # 可见字符
            if cur_vis and font.getlength(cur_vis + ch) > max_w:
                if last_space > 0:
                    head = cur[:last_space]
                    tail = cur[last_space + 1:]
                    out_lines.append(head)
                    cur = _last_color_prefix(head) + tail
                    cur_vis = _strip_color(tail)
                    last_space = cur.rfind(" ")
                else:
                    out_lines.append(cur)
                    cur = _last_color_prefix(cur)
                    cur_vis = ""
                    last_space = -1
            cur += ch
            cur_vis += ch
            if ch == " ":
                last_space = len(cur) - 1
            j += 1
        if cur:
            out_lines.append(cur)
    return "\n".join(out_lines)


def _ping_color(ping_ms):
    p = int(round(ping_ms))
    if p < 40:
        return (0, 150, 0)      # 绿
    if p <= 60:
        return (200, 150, 20)   # 黄
    return (210, 40, 40)        # 红


def _sanitize(t):
    """去掉控制字符/非法替换字符, 避免渲染出奇怪符号(如 ':(' )。保留换行。"""
    return "".join(
        ch for ch in (t or "")
        if ch in "\n\t" or (ord(ch) >= 32 and ch != "\ufffd")
    )


def _strip_color(s):
    """去掉 § 颜色/格式码, 只留可见文本(文本模式用)。"""
    s = _sanitize(s)
    s = re.sub(r"§x[0-9a-fA-F]{6}", "", s)   # §xRRGGBB
    s = re.sub(r"§.", "", s)                  # 其余 § 码 + 1 字符
    return s


def _desc_text(desc):
    """把服务器 description 转成纯文本(带 § 码), 兼容 str/dict/list 文本组件。"""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, list):
        return "".join(_desc_text(n) for n in desc)
    if isinstance(desc, dict):
        text = str(desc.get("text", ""))
        extra = desc.get("extra")
        if extra is not None:
            text += _desc_text(extra)
        return text
    return str(desc or "")


def render_card(name, version, motd, online, ping_ms, domain="", logo_bytes=None,
                intro=None, online_mode="", core="", edition="", cfg=None):
    """渲染 MC 服务器信息卡。背景 book.png, 尺寸 730x900, MOTD 彩色。cfg 控制显示项。"""
    cfg = cfg or {}
    def g(key, default):
        v = cfg.get(key, default)
        return v if v is not None else default

    W = 730
    H = 900
    TEXT = (70, 45, 20)      # 深棕
    ACCENT = (90, 60, 25)

    bg_path = os.path.join(_PLUGIN_DIR, "book.png")
    img = _load_bg(bg_path, W, H)
    d = ImageDraw.Draw(img)
    cx = W // 2

    # logo(可开关): 放在书页上, 避开上下封边框, 像记录在书上
    if g("show_logo", True):
        logo_src = logo_bytes
        if not logo_src and "基岩" in (edition or ""):
            # 基岩版协议不带 favicon, 用插件内置 logo.png 兜底
            try:
                with open(os.path.join(_PLUGIN_DIR, "logo.png"), "rb") as f:
                    logo_src = f.read()
            except Exception:
                logo_src = None
        if logo_src:
            try:
                logo = Image.open(io.BytesIO(logo_src)).convert("RGBA")
                logo = logo.resize((260, 260), Image.NEAREST)
                img.paste(logo, (cx - 130, 75), logo)
            except Exception:
                pass

    name_font = _load_font(42)
    ver_font = _load_font(28)
    motd_font = _load_font(26)
    small_font = _load_font(24)

    # 文字全部放在 logo 下方, 依显示项动态往下排(都控制在书页内)
    y = 365
    _name = _truncate_vis(_sanitize(name), 30)
    _domain = _truncate_vis(_sanitize(domain), 44)
    _motd = _sanitize(motd)
    _vers = _truncate_vis(_sanitize(version), 46)
    if g("show_name", True) and _name:
        _draw_center_colored(d, cx, y, _name, name_font, TEXT)
        y += 80
    if g("show_domain", True) and _domain:
        _draw_center_colored(d, cx, y, _domain, small_font, ACCENT)
        y += 40
    if g("show_online_mode", True) and online_mode:
        om_color = (0, 120, 0) if online_mode == "正版" else (170, 90, 20)
        d.text((cx, y), online_mode, font=small_font, fill=om_color, anchor="mm")
        y += 40
    if g("show_motd", True) and _motd:
        wrapped = _wrap_mc(_motd, motd_font, 540)
        wlines = wrapped.split("\n")
        if len(wlines) > 3:          # 最多 3 行, 超出截断(延续最后颜色)
            wrapped = "\n".join(wlines[:3]) + _last_color_prefix(wlines[2]) + "…"
            nlines = 3
        else:
            nlines = len(wlines)
        _draw_mc_center(d, cx, y + 12, wrapped, motd_font, TEXT)
        y += 12 + 34 * nlines + 4
    # Ping / 在线(分段上色, 整体居中)
    show_ping = g("show_ping", True)
    show_online = g("show_online", True)
    if show_ping or show_online:
        segs = []
        if show_ping:
            segs.append((f"Ping {int(round(ping_ms))}ms", _ping_color(ping_ms)))
        if show_ping and show_online:
            segs.append((" · ", ACCENT))
        if show_online:
            segs.append((f"在线 {online} 人", (0, 150, 0)))
        total_w = sum(d.textlength(t, font=small_font) for t, _ in segs)
        x = cx - total_w / 2
        for t, c in segs:
            d.text((x, y + 24), t, font=small_font, fill=c, anchor="lm")
            x += d.textlength(t, font=small_font)
        y += 60
    if g("show_version", True):
        core_str = f"({core})" if core else ""
        ver_line = f"版本: {_vers}§r{core_str}"
        _draw_center_colored(d, cx, y + 18, ver_line, ver_font, ACCENT)
        y += 46
    # Java版/基岩版(自动检测), 显示在版本下方
    if g("show_edition", True) and edition:
        ed_color = (60, 90, 160) if "基岩" in edition else (0, 120, 0)
        d.text((cx, y + 16), edition, font=small_font, fill=ed_color, anchor="mm")
        y += 44

    return img


def build_text(info, name, domain, intro, cfg=None):
    """非图片模式: 拼一段文本。"""
    cfg = cfg or {}
    def g(key, default):
        v = cfg.get(key, default)
        return v if v is not None else default

    lines = []
    if g("show_name", True):
        lines.append(f"名称: {_strip_color(name)}")
    if g("show_domain", True) and domain:
        lines.append(f"域名: {_strip_color(domain)}")
    if g("show_online_mode", True) and info.get("online_mode"):
        lines.append(f"在线模式: {info['online_mode']}")
    if g("show_motd", True) and info.get("motd"):
        lines.append(f"MOTD: {_strip_color(info['motd'])}")
    if g("show_ping", True) or g("show_online", True):
        p = [f"Ping: {info.get('ping_ms', 0)}ms"] if g("show_ping", True) else []
        o = [f"在线: {info.get('online', 0)} 人"] if g("show_online", True) else []
        lines.append(" · ".join(p + o))
    if g("show_version", True):
        core = info.get("core") or ""
        lines.append(f"版本: {_strip_color(info.get('version', ''))}{('(' + core + ')') if core else ''}")
    if g("show_edition", True) and info.get("edition"):
        lines.append(f"版本类型: {info['edition']}")
    if intro:
        lines.append(f"介绍: {_strip_color(intro)}")
    return "\n".join(lines)


def render_intro(intro):
    """渲染写满介绍的图(单独一张)。730x900。"""
    W = 730
    H = 900
    img = Image.new("RGB", (W, H), (245, 235, 215))
    d = ImageDraw.Draw(img)
    font = _load_font(26)
    lines = _wrap(_strip_color(intro), 26)
    y = 60
    for line in lines:
        if y > H - 40:
            break
        d.text((50, y), line, font=font, fill=(60, 30, 10))
        y += 42
    return img


def _write_varint(buf: bytearray, val: int):
    val &= 0xFFFFFFFF
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def _read_varint(data: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("varint EOF")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 35:
            raise ValueError("varint too long")


def _connect(host: str, port: int, timeout: float):
    """建立 TCP 连接。DNS 解析失败(gaierror)时重试几次, 应对瞬时 DNS 抖动。"""
    import socket
    import time as _time
    last = None
    for _ in range(3):
        try:
            return socket.create_connection((host, port), timeout=timeout)
        except socket.gaierror as e:
            last = e
            _time.sleep(0.4)
        # 非 DNS 错误(超时/拒绝等)不重试, 直接抛出
    raise last if last else OSError(f"DNS 解析失败: {host}")


def _srv_lookup(host: str, timeout: float = 2.0):
    """查询 Minecraft 服务的 SRV 记录 `_minecraft._tcp.<host>`, 返回 (target_host, port) 或 None。

    依次尝试: 系统 DNS → 国内公共 DNS(阿里/腾讯) → 国外公共 DNS, 兼容不同网络环境。
    """
    try:
        import dns.message
        import dns.query
        import dns.rdatatype
        import dns.name
    except Exception:
        return None
    try:
        qname = dns.name.from_text("_minecraft._tcp." + host + ".")
        q = dns.message.make_query(qname, dns.rdatatype.SRV)
    except Exception:
        return None
    servers = []
    try:
        import dns.resolver
        servers += list(dns.resolver.get_default_resolver().nameservers)
    except Exception:
        pass
    servers += ["223.5.5.5", "119.29.29.29", "1.1.1.1", "8.8.8.8"]
    for s in dict.fromkeys(servers):
        try:
            resp = dns.query.udp(q, s, timeout=timeout)
        except Exception:
            continue
        try:
            for rr in resp.answer:
                for item in rr:
                    target = str(item.target).rstrip(".")
                    return (target, int(item.port))
        except Exception:
            continue
    return None


def _try_srv(host: str, port: int):
    """主机名无法直接解析时, 用 SRV 记录替换 host/port。返回 (host, port)。"""
    import socket
    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        return (host, port)          # 能解析, 直接用
    except Exception:
        pass
    srv = _srv_lookup(host)
    if srv:
        return srv
    return (host, port)


def _mc_srp_query(host: str, port: int, protocol: int = 47, timeout: float = 6.0):
    """纯 TCP Server List Ping(同步, 需在 executor 中调用)。

    构造 handshake + status request, 解析返回 JSON(不依赖 mcstatus, 兼容
    BungeeCord/屏蔽 status 但 handshake 正常的服务器)。
    """
    import socket

    sock = _connect(host, port, timeout)
    try:
        sock.settimeout(timeout)

        # ---- handshake (0x00), next_state 1 (status) ----
        host_b = host.encode("utf-8")
        hb = bytearray()
        hb.append(0x00)
        _write_varint(hb, protocol)        # protocol version
        _write_varint(hb, len(host_b))     # address length
        hb += host_b
        hb += port.to_bytes(2, "big")    # port
        hb.append(0x01)                  # next_state: 1 (status)
        pkt = bytearray()
        _write_varint(pkt, len(hb))      # packet length
        pkt += hb

        # ---- status request (0x00) ----
        import time
        req = bytearray()
        _write_varint(req, len(b"\x00"))
        req += b"\x00"
        pkt += req

        t0 = time.perf_counter()
        sock.sendall(bytes(pkt))

        # ---- read response ----
        def recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise ValueError("connection closed")
                buf += chunk
            return buf

        # 读包长 VarInt(可能多字节): 逐字节累积直到连续位为 0
        len_buf = b""
        while True:
            b = recv_exact(1)
            len_buf += b
            if not (b[0] & 0x80):
                break
            if len(len_buf) > 5:
                raise ValueError("packet length varint too long")
        pkt_len, _ = _read_varint(len_buf, 0)
        pkt_body = recv_exact(pkt_len)

        # 包体内第一个字段是包ID(varint), 后面是 JSON 字符串
        _, pos = _read_varint(pkt_body, 0)
        jstr_len, pos = _read_varint(pkt_body, pos)
        raw = pkt_body[pos:pos + jstr_len].decode("utf-8", "replace")
        data = json.loads(raw)
        ping_ms = int((time.perf_counter() - t0) * 1000)
        return data, ping_ms
    finally:
        try:
            sock.close()
        except Exception:
            pass


# 探测在线模式时回退用的协议版本(按从新到旧)
_LOGIN_PROTOCOLS = (765, 760, 754, 340, 47)


def _mc_login_probe(host: str, port: int, protocol: int = 765, timeout: float = 3.0):
    """用 login(next_state=2) 握手探测服务器是否正版验证(在线模式)。

    返回:
      "online"  —— 服务器首先回 Set Encryption Request(0x01) ⇒ 正版验证
      "offline" —— 服务器直接回 Login Success(0x02) ⇒ 离线(cracked)
      None      —— 无法判定(断开/超时/未知包)
    """
    import socket

    if not protocol:
        protocol = 765
    sock = _connect(host, port, timeout)
    try:
        sock.settimeout(timeout)

        host_b = host.encode("utf-8")
        hb = bytearray()
        hb.append(0x00)                       # handshake
        _write_varint(hb, protocol)           # protocol version
        _write_varint(hb, len(host_b))
        hb += host_b
        hb += port.to_bytes(2, "big")
        hb.append(0x02)                       # next_state: login
        pkt = bytearray()
        _write_varint(pkt, len(hb))
        pkt += hb
        sock.sendall(bytes(pkt))

        def recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise ValueError("connection closed")
                buf += chunk
            return buf

        # 读包长 VarInt + 包体
        len_buf = b""
        while True:
            b = recv_exact(1)
            len_buf += b
            if not (b[0] & 0x80):
                break
            if len(len_buf) > 5:
                raise ValueError("packet length varint too long")
        pkt_len, _ = _read_varint(len_buf, 0)
        body = recv_exact(pkt_len)
        pid, _ = _read_varint(body, 0)
        if pid == 0x01:
            return "online"      # Set Encryption Request ⇒ 正版
        if pid == 0x02:
            return "offline"     # Login Success ⇒ 离线
        return None              # Disconnect(0x00) 或其他 ⇒ 未知
    finally:
        try:
            sock.close()
        except Exception:
            pass


# RakNet Unconnected Ping 的 magic
_RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def _bedrock_query(host: str, port: int, timeout: float = 3.0):
    """用 RakNet Unconnected Ping 探测并解析基岩版(Bedrock)服务器。

    返回 dict(edition/motd/version/online/max), 不是基岩服返回 None。
    """
    import socket
    import struct

    # 先解析 host(带 DNS 重试), 避免 UDP 发送时 getaddrinfo 失败
    try:
        ip = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
    except Exception:
        return None
    data = b"\x01" + b"\x00" * 8 + _RAKNET_MAGIC   # Unconnected Ping
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        import time as _time
        t0 = _time.perf_counter()
        s.sendto(data, (ip, port))
        resp, _ = s.recvfrom(2048)
        ping_ms = int((_time.perf_counter() - t0) * 1000)
        if len(resp) < 35 or resp[0] != 0x1C or resp[17:33] != _RAKNET_MAGIC:
            return None
        ln = struct.unpack(">H", resp[33:35])[0]
        name = resp[35:35 + ln].decode("utf-8", "replace")
        parts = name.split(";")
        if parts and parts[0].upper() == "MCPE":
            def _num(idx):
                if len(parts) <= idx:
                    return 0
                m = re.search(r"\d+", parts[idx])
                try:
                    return int(m.group(0)) if m else 0
                except Exception:
                    return 0
            return {
                "edition": "基岩版",
                "motd": parts[1] if len(parts) > 1 else "",
                "version": parts[3] if len(parts) > 3 else "",
                "online": _num(4),
                "max": _num(5),
                "protocol": parts[2] if len(parts) > 2 else "",
                "ping_ms": ping_ms,
            }
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass
    return None


async def _detect_online_mode(host: str, port: int, protocol, probe_timeout: float = 3.0) -> str:
    """探测在线模式。返回 '正版' / '离线' / ''(无法判定)。best-effort。

    并发探测(服务的实际协议 + 常用回退协议), 避免串行循环拖慢查询, 单次最多约 probe_timeout。
    """
    loop = asyncio.get_event_loop()
    protos = []
    if protocol:
        protos.append(protocol)
    if 47 not in protos:
        protos.append(47)  # 1.8 常用协议, 兼容老服务器

    async def _one(p):
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _mc_login_probe, host, port, p),
                timeout=probe_timeout,
            )
        except Exception:
            return None

    results = await asyncio.gather(*(_one(p) for p in protos))
    for r in results:
        if r == "online":
            return "正版"
        if r == "offline":
            return "离线"
    return ""


# 服务器核心识别关键词(从上到下匹配, 越靠前越优先)
_CORE_KEYWORDS = ["NeoForge", "Folia", "Forge", "Paper", "Purpur", "Fabric", "Spigot", "Bukkit"]


def _detect_core(version, motd, raw=None):
    """尽可能识别服务器核心(mod loader/软件)。返回核心名, 无法识别返回 ''(不显示)。"""
    if isinstance(raw, dict):
        mi = raw.get("modinfo")
        if isinstance(mi, dict):
            t = str(mi.get("type", "")).lower()
            if "neoforge" in t:
                return "NeoForge"
            if t in ("fml", "forge", "forge"):
                return "Forge"
    blob = (str(version or "") + " " + str(motd or "")).lower()
    for name in _CORE_KEYWORDS:
        if name.lower() in blob:
            return name
    return ""


async def query_server(domain, timeout: int = 8):
    """查询服务器, 返回 dict。mcstatus 失败则回退到纯 TCP SRP。"""
    probe_timeout = max(2.0, min(3.0, timeout))  # 在线模式探测单独设一个上限
    # 拆 host/port(支持 host:端口)
    host = domain
    port = 25565
    if ":" in domain:
        host, _, port_s = domain.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            port = 25565
    if not (1 <= port <= 65535):
        port = 25565   # 防端口越界导致 to_bytes 溢出

    # 主机名无法直接解析时, 用 Minecraft SRV 记录替换 host/port(如 mc.cneko.org → vat-n66.mutong1.com:21005)
    loop = asyncio.get_event_loop()
    try:
        host, port = await asyncio.wait_for(
            loop.run_in_executor(None, _try_srv, host, port), timeout=6
        )
    except Exception:
        pass

    # ---- 优先 mcstatus ----
    try:
        from mcstatus import JavaServer  # mcstatus 11+
    except ImportError:
        try:
            from mcstatus import MinecraftServer as JavaServer  # 旧版
        except Exception:
            JavaServer = None

    if JavaServer is not None:
        try:
            server = JavaServer(host, port)
            status = await asyncio.wait_for(server.async_status(), timeout=timeout)
            favicon_b64 = getattr(status, "favicon", None)
            logo_bytes = None
            if favicon_b64:
                try:
                    logo_bytes = base64.b64decode(favicon_b64.split(",", 1)[-1])
                except Exception:
                    logo_bytes = None
            motd = _desc_text(getattr(status, "description", "") or "")
            ver_obj = getattr(status, "version", None)
            version = getattr(ver_obj, "name", "") if ver_obj else ""
            protocol = getattr(ver_obj, "protocol", None) if ver_obj else None
            players = getattr(status, "players", None)
            online = getattr(players, "online", 0) if players else 0
            maxp = getattr(players, "max", 0) if players else 0
            latency = getattr(status, "latency", 0) or 0
            online_mode = await _detect_online_mode(host, port, protocol, probe_timeout)
            core = _detect_core(version, motd, getattr(status, "raw", None))
            return {
                "version": version,
                "motd": motd,
                "online": online,
                "max": maxp,
                "ping_ms": int(latency),
                "logo": logo_bytes,
                "online_mode": online_mode,
                "core": core,
                "edition": "java版",
            }
        except Exception as e:
            logger.error(f"mcstatus 查询 {host}:{port} 失败: {e!r} · 回退 SRP")

    # ---- 回退: 纯 TCP SRP(依次试多个协议版本, 兼容部分 mod/代理服) ----
    loop = asyncio.get_event_loop()

    async def _run_srp(proto):
        return await loop.run_in_executor(None, _mc_srp_query, host, port, proto)

    srp = None
    for proto in (47, 765, 760):  # 1.8 / 1.20.4 / 1.20.2
        try:
            srp = await asyncio.wait_for(_run_srp(proto), timeout=timeout)
        except Exception as e:
            logger.error(f"SRP {host}:{port} 协议{proto} 失败: {e!r}")
            srp = None
            continue
        if isinstance(srp, tuple) and len(srp) == 2 and isinstance(srp[0], dict):
            break
        srp = None
    if srp is None:
        # ---- Java 失败, 尝试基岩版(Bedrock) RakNet ping ----
        try:
            bd = await asyncio.wait_for(
                loop.run_in_executor(None, _bedrock_query, host, port),
                timeout=min(timeout, 6),
            )
        except Exception:
            bd = None
        if bd and isinstance(bd, dict):
            bd.setdefault("ping_ms", 0)
            bd["logo"] = None
            bd["online_mode"] = ""
            bd["core"] = ""
            return bd
        return None
    data, ping_ms = srp
    if not isinstance(data, dict):
        return None

    desc = data.get("description", {})
    motd = _desc_text(desc)

    ver = data.get("version", {}) or {}
    players = data.get("players", {}) or {}
    version = ver.get("name", "") if isinstance(ver, dict) else str(ver)
    protocol = ver.get("protocol") if isinstance(ver, dict) else None
    logo64 = data.get("favicon") or ""
    logo_bytes = None
    if logo64:
        try:
            logo_bytes = base64.b64decode(logo64.split(",", 1)[-1])
        except Exception:
            logo_bytes = None
    online_mode = await _detect_online_mode(host, port, protocol, probe_timeout)
    core = _detect_core(version, motd, data)
    return {
        "version": version,
        "motd": motd,
        "online": (players.get("online", 0) if isinstance(players, dict) else 0),
        "max": (players.get("max", 0) if isinstance(players, dict) else 0),
        "ping_ms": ping_ms,
        "logo": logo_bytes,
        "online_mode": online_mode,
        "core": core,
        "edition": "java版",
    }



# ---- 域名解析: 兼容 AstrBot 注入的 [msg_id:...] 等方括号标记 ----
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
_DOMAIN_RE = re.compile(
    r"("
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}"  # 域名
    r"|(?:\d{1,3}(?:\.\d{1,3}){3})"                              # IPv4
    r")(?::(\d{1,5}))?"
)


def _clean_msg(text: str) -> str:
    """去掉 [xxxx:yyy] / [CQ:...] 等方括号标记(消息id、引用等), 避免被当成域名/介绍。"""
    return _BRACKET_RE.sub(" ", text or "")


def _find_domain(text: str) -> str:
    """从(已清洗)文本里找第一个合法主机名(可带端口)。返回 'host' 或 'host:port', 找不到返回空。"""
    m = _DOMAIN_RE.search(_clean_msg(text))
    if not m:
        return ""
    host = m.group(1)
    port = m.group(2)
    return f"{host}:{port}" if port else host


def _is_domain(text: str) -> bool:
    """text 本身是否是一个合法域名(可带端口)。"""
    return bool(_DOMAIN_RE.fullmatch((text or "").strip()))


# ============ 插件 ============
@register("mc_query", "KUNFAN-666", "MC 服务器查询 - /mc 显示信息并渲染图片", "1.2.1")
class MCQueryPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        # AstrBot 通过 config= 把 _conf_schema.json 的插件配置传给构造函数
        self.config = config if isinstance(config, dict) else (config or {})
        self._store = ensure_store()
        self._session_last: dict[str, str] = {}

    def _cfg(self, key: str, default=None):
        """读取插件配置, 缺省时返回 default。"""
        try:
            v = self.config.get(key, default)
        except Exception:
            v = default
        return v if v is not None else default

    def _retry_count(self) -> int:
        """读取 /mc 查询失败重试次数(0~5)。"""
        try:
            n = int(self._cfg("mc_query_retry", 2) or 2)
        except Exception:
            n = 2
        return max(0, min(5, n))

    def _query_timeout(self) -> int:
        try:
            t = int(self._cfg("query_timeout", 8) or 8)
        except Exception:
            t = 8
        return max(3, min(20, t))

    def _is_bool(self, key: str, default: bool = True) -> bool:
        v = self._cfg(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "是")
        return bool(v)


    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """是否 AstrBot 管理员。优先 event.is_admin()(WakingCheckStage 已用全局 admins_id 设置 role)。"""
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        # 兜底: 手动比对全局配置 admins_id
        try:
            cfg = self.context.get_config()
            admins = cfg.get("admins_id", []) if hasattr(cfg, "get") else []
            sender = str(event.get_sender_id())
            return any(str(a) == sender for a in admins)
        except Exception:
            return False

    @filter.command("mcadd")
    async def mcadd(self, event: AstrMessageEvent):
        """添加服务器: /mcadd <名称> <域名> [介绍]  (仅 AstrBot 管理员)"""
        # 仅管理员可用
        if not self._is_admin(event):
            yield event.plain_result("你没有使用 /mcadd 的权限(仅 AstrBot 管理员可用)。")
            return
        # 清洗掉 AstrBot 注入的 [msg_id:...] 等标记, 再定位命令与域名
        content = _clean_msg(event.message_str or "")
        m = re.search(r"(?:^|\s)(?:/)?mcadd\b", content, re.I)
        seg = content[m.end():].strip() if m else content.strip()
        domain = _find_domain(seg) or _find_domain(content)
        if not domain:
            yield event.plain_result("用法: /mcadd <名称> <域名> [介绍]  示例: /mcadd 生存服 mc.example.com")
            return
        if domain in seg:
            pre = seg.split(domain, 1)[0].strip()
            intro = seg.split(domain, 1)[1].strip()
        else:
            pre = seg.strip()
            intro = ""
        name = pre.split()[0] if pre.strip() else domain
        store = self._store
        domain = domain.strip().lower()
        store["servers"][domain] = {"name": name, "domain": domain, "intro": intro}
        sid = session_of(event)
        store["session_default"][sid] = domain
        store["session_default"].pop("", None)
        self._session_last[sid] = domain
        save_store(store)
        yield event.plain_result(f"已添加服务器「{name}」({domain})。发送 /mc 即可查询。")

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        """查询服务器: /mc [域名]  (不带域名用本会话上次 /mcadd 的)"""
        sid = session_of(event)
        # 清洗注入标记, 定位命令与域名
        content = _clean_msg(event.message_str or "")
        m = re.search(r"(?:^|\s)(?:/)?mc\b", content, re.I)
        seg = content[m.end():].strip() if m else content.strip()
        domain = _find_domain(seg) or _find_domain(content)
        if not domain:
            domain = self._store["session_default"].get(sid) or self._session_last.get(sid) or ""
            if not domain or not _is_domain(domain):
                # 清理可能的历史脏数据
                self._store["session_default"].pop(sid, None)
                self._session_last.pop(sid, None)
                yield event.plain_result("请先 /mcadd 添加服务器, 或使用 /mc <域名>")
                return
        # 已添加的用其名称/介绍; 未添加的直接用域名查询
        domain = domain.lower()
        s = self._store["servers"].get(domain, {"name": domain, "domain": domain, "intro": ""})
        # 查询失败自动重试(次数可在插件配置 mc_query_retry 修改)
        attempts = 1 + self._retry_count()
        info = None
        for i in range(attempts):
            try:
                info = await query_server(domain, timeout=self._query_timeout())
            except Exception as e:
                logger.error(f"查询 {domain} 异常: {e!r}")
                info = None
            if info:
                break
            if i < attempts - 1:
                await asyncio.sleep(1.5)
        if not info:
            yield event.plain_result(self._cfg("query_failed_text") or "无法查询到该服务器(可能离线或域名错误)。")
            return
        self._session_last[sid] = domain
        intro_text = s.get("intro") or ""
        render_as_image = self._is_bool("render_as_image", True)
        if not render_as_image:
            # 文本模式
            yield event.plain_result(build_text(info, s["name"], domain, intro_text, self.config))
            return
        img = render_card(
            name=s["name"],
            version=info["version"],
            motd=info["motd"],
            online=info["online"],
            ping_ms=info["ping_ms"],
            domain=domain,
            logo_bytes=info["logo"],
            online_mode=info.get("online_mode", ""),
            core=info.get("core", ""),
            edition=info.get("edition", ""),
            cfg=self.config,
        )
        path = os.path.join(_DATA_DIR, "mc_query", f"card_{domain.replace('.','_')}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path)
        img.close()
        yield event.image_result(path)
        # 添加者写了介绍 → 额外发一张写满介绍的图
        if intro_text:
            intro_img = render_intro(intro_text)
            ipath = os.path.join(_DATA_DIR, "mc_query", f"intro_{domain.replace('.','_')}.png")
            intro_img.save(ipath)
            intro_img.close()
            yield event.image_result(ipath)

    async def terminate(self):
        pass
