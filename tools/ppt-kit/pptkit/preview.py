# -*- coding: utf-8 -*-
"""把 pptx 渲成逐页 png，供目视复查。

    python -m pptkit.preview out.pptx -o shots

依赖：
  - Windows + Microsoft PowerPoint（用 COM 导出 PDF）
  - poppler 的 pdftoppm（TeX Live 自带的也能用）

两条外部依赖缺任一条就报错退出，不做静默降级——渲染不出来就该知道。
"""
import argparse
import base64
import glob
import os
import shutil
import subprocess
import sys

PS_TEMPLATE = """
$ErrorActionPreference = 'Stop'
$src = '{src}'
$pdf = '{pdf}'
try {{
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($src, $true, $false, $false)
    $pres.SaveAs($pdf, 32)
    $pres.Close()
    $app.Quit()
}} catch {{
    Write-Error $_.Exception.Message
    exit 1
}}
"""


def _dec(b: bytes) -> str:
    """子进程输出在中文 Windows 上是 GBK，不要用 utf-8 硬解。"""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace").strip()


def _find_pdftoppm() -> str:
    p = shutil.which("pdftoppm")
    if p:
        return p
    for pat in (r"C:\texlive\*\bin\windows\pdftoppm.exe",
                r"D:\texlive\*\bin\windows\pdftoppm.exe",
                r"C:\Program Files\poppler*\bin\pdftoppm.exe"):
        hit = glob.glob(pat)
        if hit:
            return hit[0]
    raise FileNotFoundError(
        "找不到 pdftoppm。装 poppler（或 TeX Live 完整版）后重试，"
        "或手动用 PowerPoint 导出 PDF 再转图。"
    )


def _to_pdf(pptx: str, pdf: str) -> None:
    if os.name != "nt":
        raise OSError("_to_pdf 目前只支持 Windows + PowerPoint")
    if os.path.exists(pdf):
        os.remove(pdf)
    script = PS_TEMPLATE.format(src=pptx.replace("'", "''"),
                                pdf=pdf.replace("'", "''"))
    # -EncodedCommand 走 UTF-16LE，中文路径不会乱码
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
        capture_output=True, timeout=180,
    )
    if r.returncode != 0 or not os.path.exists(pdf):
        raise RuntimeError("PowerPoint 导出 PDF 失败：" + _dec(r.stderr)[:300])


def render(pptx: str, out_dir: str = "shots", dpi: int = 100) -> list:
    pptx = os.path.abspath(pptx)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    pdf = os.path.join(os.path.dirname(pptx),
                       os.path.splitext(os.path.basename(pptx))[0] + ".preview.pdf")
    _to_pdf(pptx, pdf)

    pdftoppm = _find_pdftoppm()
    prefix = os.path.join(out_dir, "s")
    r = subprocess.run([pdftoppm, "-png", "-r", str(dpi), pdf, prefix],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("pdftoppm 失败：" + _dec(r.stderr)[:300])
    files = sorted(glob.glob(prefix + "*.png"))
    print(f"[ok] {len(files)} 页 → {out_dir}")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="pptx → 逐页 png")
    ap.add_argument("pptx")
    ap.add_argument("-o", "--out", default="shots")
    ap.add_argument("--dpi", type=int, default=100)
    a = ap.parse_args()
    render(a.pptx, a.out, a.dpi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
