#!/usr/bin/env python3
"""
方案 A 验证：直接读 Chrome Cookie 库 + 解密，与 cookies.json（CDP 导入基准）三方比对。
跨平台结构：darwin（钥匙串 + v10 AES-CBC，本机实测）/ win32（DPAPI，占位待实测）。
用法：python3 xhs_cookie_file_read.py [--compare data/cookies.json]
"""
import argparse, base64, json, os, platform, sqlite3, subprocess, sys
from datetime import datetime

def darwin_key():
    """从钥匙串读取 Chrome Safe Storage 密钥（授权后不再弹窗）"""
    r = subprocess.run(
        ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(f"钥匙串读取失败 rc={r.returncode}: {r.stderr.strip()[:200]}")
    # Chrome macOS: PBKDF2 的 password 直接用 base64 字符串本身，不解码！
    return r.stdout.strip().encode()

def win32_key():
    """Windows: DPAPI 解密 Local State 的 encrypted_key（占位，待 Windows 实测）"""
    local = os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data")
    with open(os.path.join(local, "Local State"), encoding="utf-8") as f:
        enc = base64.b64decode(json.load(f)["os_crypt"]["encrypted_key"])
    # DPAPI 前缀 "DPAPI"
    import ctypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]
    blob_in, blob_out = DATA_BLOB(), DATA_BLOB()
    buf = ctypes.create_string_buffer(enc[5:])
    blob_in.cbData, blob_in.pbData = len(enc) - 5, ctypes.addressof(buf)
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI 解密失败")
    key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return key

def decrypt_v10(key, blob, strip_integrity=False):
    """v10: PBKDF2(SHA1, 'saltysalt', 1003) 16B + AES-CBC(iv=16空格)。
    Chrome 库 version>=24：密文 = AES-CBC(域名SHA256前缀32B + cookie值 + PKCS7)。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    assert blob[:3] == b"v10", f"非 v10 头: {blob[:3]}"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    dk = kdf.derive(key)  # key = base64 字符串 bytes（Chrome macOS 不做解码）
    iv = b" " * 16
    dec = Cipher(algorithms.AES(dk), modes.CBC(iv)).decryptor().update(blob[3:])
    plain = dec[:-dec[-1]]  # 去 PKCS7 padding
    if strip_integrity:
        plain = plain[32:]  # 去掉域名 SHA256 完整性前缀
    return plain

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", default="", help="cookies.json 路径（比对基准）")
    args = ap.parse_args()

    key = darwin_key() if platform.system() == "Darwin" else win32_key()
    print(f"[key] base64 字符串 len={len(key)}B ({platform.system()})")

    db = os.path.expanduser(
        "~/Library/Application Support/Google/Chrome/Default/Cookies"
        if platform.system() == "Darwin" else
        os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data", "Default", "Cookies")
    )
    con = sqlite3.connect(db)
    try:
        meta_ver, = con.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        strip_integrity = int(meta_ver) >= 24
    except Exception:
        strip_integrity = False
    print(f"[meta] cookie 库 version={meta_ver if 'meta_ver' in dir() else '?'} | 剥离32B完整性前缀: {strip_integrity}")
    rows = con.execute(
        "SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure FROM cookies "
        "WHERE host_key LIKE '%xiaohongshu.com%' ORDER BY host_key, name"
    ).fetchall()
    print(f"[db] 小红书 cookie 行数: {len(rows)}")

    decrypted = []
    for host, name, value, enc_value, path, exp, secure in rows:
        blob = enc_value if isinstance(enc_value, bytes) else (enc_value.encode("latin1") if enc_value else b"")
        if not blob or not blob.startswith(b"v10"):
            # 未加密的明文 cookie
            decrypted.append((host, name, value or "", path, exp, secure))
            continue
        try:
            plain = decrypt_v10(key, blob, strip_integrity)
            decrypted.append((host, name, plain.decode("utf-8"), path, exp, secure))
        except Exception as e:
            decrypted.append((host, name, f"<DECRYPT_FAIL: {e}>", path, exp, secure))

    # 比对基准：cookies.json
    if args.compare:
        ref = json.load(open(args.compare, encoding="utf-8"))["cookies"]
        refmap = {}
        for c in ref:
            dom = (c.get("domain") or "").lstrip(".")
            refmap[(dom, c.get("name"))] = (c.get("value", ""), c.get("expirationDate", ""))
        print(f"[ref] cookies.json: {len(ref)} 条")
        print("\n=== 比对表（文件解密 ↔ cookies.json）===")
        print(f"{'host/name':<42} {'一致':<4} {'文件value前20':<22} {'json value前20':<22} {'到期'}")
        matched = mismatched = missing_in_json = extra_in_file = 0
        for host, name, plain, path, exp, secure in decrypted:
            dom = host.lstrip(".")
            is_match = refmap.get((dom, name))
            if is_match:
                ok = is_match[0] == plain
                if ok:
                    matched += 1
                else:
                    mismatched += 1
                    print(f"{dom}/{name:<30} ✗不一致  {plain[:20]:<22} {is_match[0][:20]:<22} 到期={is_match[1]}")
            else:
                missing_in_json += 1
                print(f"{dom}/{name:<30} 仅文件有（json 无此条目） {plain[:30]}")
        print(f"\n[结果] 一致 {matched} 条 | 不一致 {mismatched} 条 | 文件独有 {missing_in_json} 条")
        print("（cookies.json 独有但文件无的：")
        keys_in_file = {(c[0].lstrip("."), c[1]) for c in decrypted}
        only_json = [k for k in refmap if k not in keys_in_file]
        print(f"  {len(only_json)} 条: {[k[1] for k in only_json][:10]}）")
    else:
        for host, name, plain, path, exp, secure in decrypted:
            print(f"{host} | {name} | {plain[:40]} | exp={exp}")

if __name__ == "__main__":
    main()
