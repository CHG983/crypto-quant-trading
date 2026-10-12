#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dom_measure.py — 以 agent-browser 在指定視窗寬度下量測報告頁的實際版面。

輸出（每種寬度各一份）：
  * document.scrollWidth / innerWidth  → 頁面層級橫向溢出
  * 每張圖 naturalWidth / complete      → 是否破圖
  * 逐一量測所有可見元素，找出右緣超出視窗者 → 版面溢出
  * 文字重疊偵測：同層兄弟節點中「水平與垂直皆有交集」且皆含文字者
"""
from __future__ import annotations

import json
import subprocess
import sys

AB = "/usr/bin/agent-browser"

MEASURE_JS = r"""
(() => {
  const W = window.innerWidth, H = window.innerHeight;
  const de = document.documentElement;

  // 1) 頁面層級溢出
  const pageOverflow = Math.max(0, de.scrollWidth - W);

  // 2) 圖片解碼狀態
  const imgs = [...document.images].map(i => ({
    src: i.getAttribute('src'),
    complete: i.complete,
    nw: i.naturalWidth,
    nh: i.naturalHeight,
    broken: i.complete && i.naturalWidth === 0
  }));

  // 3) 右緣超出視窗的元素（排除位於可橫向捲動／裁切容器內者 —— 那是設計上允許的）
  const inScrollable = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      const ox = cs.overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
      p = p.parentElement;
    }
    return false;
  };
  const offenders = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return;
    if (r.right > W + 1 && !inScrollable(el)) {
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : '',
        right: Math.round(r.right),
        width: Math.round(r.width),
        text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 50)
      });
    }
  });

  // 4) 文字重疊：同層兄弟中「同時水平與垂直交疊」且兩者皆有直接文字
  //    回報 outerHTML 片段與雙方 rect，以便人為判斷是否為真缺陷。
  const overlaps = [];
  const hasText = el => [...el.childNodes].some(n =>
    n.nodeType === 3 && n.textContent.trim().length > 1);
  const rectOf = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)]; };
  document.querySelectorAll('*').forEach(parent => {
    const kids = [...parent.children].filter(k => {
      const r = k.getBoundingClientRect();
      const cs = getComputedStyle(k);
      return r.width > 2 && r.height > 2 && cs.visibility !== 'hidden' &&
             cs.display !== 'none' && cs.position !== 'absolute' && cs.position !== 'fixed';
    });
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ox > 2 && oy > 2) {
          overlaps.push({
            parent: parent.tagName.toLowerCase() + '.' + String(parent.className || '').slice(0, 30),
            a: kids[i].outerHTML.slice(0, 70), b: kids[j].outerHTML.slice(0, 70),
            aRect: rectOf(kids[i]), bRect: rectOf(kids[j]),
            overlap: [Math.round(ox), Math.round(oy)]
          });
        }
      }
    }
  });

  // 5) 表格 / 程式碼區塊是否落在可橫向捲動容器內
  const wrappers = [...document.querySelectorAll('.tablewrap')].map(w => ({
    scrollW: w.scrollWidth, clientW: w.clientWidth,
    scrollable: w.scrollWidth > w.clientWidth + 1
  }));

  return JSON.stringify({
    vw: W, vh: H, pageOverflow,
    imgTotal: imgs.length, imgBroken: imgs.filter(i => i.broken).length,
    imgs: imgs.slice(0, 3),
    offendersTotal: offenders.length,
    offenders: offenders.slice(0, 12),
    overlapTotal: overlaps.length,
    overlaps: overlaps.slice(0, 8),
    tablewrapTotal: wrappers.length,
    tablewrapScrollable: wrappers.filter(w => w.scrollable).length
  });
})()
"""


def run(url: str, w: int, h: int, label: str) -> dict:
    subprocess.run([AB, "open", url], capture_output=True, text=True, timeout=180)
    subprocess.run([AB, "set", "viewport", str(w), str(h)],
                   capture_output=True, text=True, timeout=60)
    subprocess.run([AB, "open", url], capture_output=True, text=True, timeout=180)
    r = subprocess.run([AB, "eval", MEASURE_JS], capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    start, end = out.find("{"), out.rfind("}")
    if start < 0:
        print(f"[{label}] 量測失敗，原始輸出：\n{out[:800]}")
        return {}
    # eval 可能回傳 JSON 字串（含跳脫）—— 取出最外層物件
    try:
        data = json.loads(out[start:end + 1])
        if isinstance(data, str):
            data = json.loads(data)
    except json.JSONDecodeError as e:
        print(f"[{label}] JSON 解析失敗：{e}\n{out[:800]}")
        return {}
    print(f"\n{'=' * 70}\n  {label}  (viewport {w}x{h})\n{'=' * 70}")
    print(f"  頁面層級橫向溢出 : {data['pageOverflow']} px   {'✅' if data['pageOverflow'] == 0 else '❌'}")
    print(f"  圖片解碼         : {data['imgTotal'] - data['imgBroken']}/{data['imgTotal']} 成功   "
          f"{'✅' if data['imgBroken'] == 0 else '❌'}")
    print(f"  右緣超出元素     : {data['offendersTotal']} 個   "
          f"{'✅' if data['offendersTotal'] == 0 else '❌'}")
    for o in data["offenders"]:
        print(f"      - <{o['tag']} class=\"{o['cls']}\"> right={o['right']} w={o['width']} | {o['text']}")
    print(f"  文字重疊         : {data['overlapTotal']} 組   "
          f"{'✅' if data['overlapTotal'] == 0 else '❌'}")
    for o in data["overlaps"]:
        print(f"      - {o['parent']} : {o['a']} × {o['b']} 重疊 {o['overlap']}px")
    print(f"  表格捲動容器     : {data['tablewrapTotal']} 個，其中 {data['tablewrapScrollable']} 個可橫向捲動")
    data["label"] = label
    return data


def main() -> int:
    url = sys.argv[1]
    results = []
    for url_i, w, h, label in ((url, 1280, 900, "桌面 1280"), (url, 390, 844, "手機 390")):
        d = run(url_i, w, h, label)
        if d:
            results.append(d)

    bad = [d for d in results if d["pageOverflow"] > 0 or d["imgBroken"] > 0
           or d["offendersTotal"] > 0 or d["overlapTotal"] > 0]
    print(f"\n{'=' * 70}")
    print(f"  總計 {'全部通過 ✅' if not bad else f'{len(bad)} 種寬度有缺陷 ❌'}")
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())