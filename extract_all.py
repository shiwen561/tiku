#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unified PDF question extractor — replaces three separate scripts.

Performance fixes vs original:
  - Pre-compiled regex (all patterns compiled once)
  - Split-based answer/option extraction (no re.DOTALL backtracking)
  - Single load/save of questions.json
"""

import pymupdf
import json
import re
import os

# ── Pre-compiled regex ─────────────────────────────────────

_re_header1 = re.compile(r'学员专用资料.*?第\d+页/共\d+页')
_re_header2 = re.compile(r'环球网校学员专用')
_re_header3 = re.compile(r'课程咨询：\d*')
_re_page = re.compile(r'第\s*\d+\s*页\s*/\s*共\s*\d+\s*页')
_re_multi_nl = re.compile(r'\n{3,}')
_re_multi_sp = re.compile(r'[ \t]+')
_re_nl_sp = re.compile(r'\n +')
_re_standalone = re.compile(r'\n\d+\n')
_re_year = re.compile(r'(20\d{2})')
_re_year_cn = re.compile(r'(20\d{2})年')
_re_qnum = re.compile(r'\n(\d{1,2})[.、．]\s*')
_re_opt = re.compile(r'^([A-E])[.、．]\s*(.+)$')
_re_answer_clean = re.compile(r'[^A-Ea-e]')
_re_answer_prefix = re.compile(r'选项[A-E]')
_re_judge_at = re.compile(r'\nA[.、．]\s*对\b.*|\nA[.、．]\s*正确\b.*')
_re_judge_bt = re.compile(r'\nB[.、．]\s*错\b.*|\nB[.、．]\s*错误\b.*')
_re_requirement = re.compile(r'要求[：:]\s*')
_re_s45 = re.compile(r'[四五][、，.]')
_re_non_answer = re.compile(r'[^A-E]')

# ── Subject configs ────────────────────────────────────────

SUBJECTS = [
    {
        'pdf_dir': r'D:\桌面\RAG数据库\中级会计实务',
        'category': '中级会计实务',
        'counts': {'单选题': 10, '多选题': 10, '判断题': 10, '计算分析题': 2, '综合题': 2},
        'fill': True,
        'sec4_type': '计算分析题',
        'sec4_re': re.compile(r'四[、，.]\s*(?:计算|計算)'),
        'sec5_re': re.compile(r'五[、，.]\s*(?:综合|綜合)'),
    },
    {
        'pdf_dir': r'D:\桌面\RAG数据库\中级经济法',
        'category': '中级经济法',
        'counts': {'单选题': 30, '多选题': 15, '判断题': 10, '简答题': 3, '综合题': 1},
        'fill': True,
        'sec4_type': '简答题',
        'sec4_re': re.compile(r'四[、，.]\s*(?:简答|簡答)'),
        'sec5_re': re.compile(r'五[、，.]\s*(?:综合|綜合)'),
    },
    {
        'pdf_dir': r'D:\桌面\RAG数据库\中级会计财务管理',
        'category': '中级财务管理',
        'counts': {},  # no fixed counts
        'fill': False,
        'sec4_type': '计算分析题',
        'sec4_re': re.compile(r'四[、，.]\s*(?:计算|計算)'),
        'sec5_re': re.compile(r'五[、，.]\s*(?:综合|綜合)'),
    },
]

_sec_re_1 = re.compile(r'一[、，.]\s*(?:单项|单选|單項|單選)')
_sec_re_2 = re.compile(r'二[、，.]\s*(?:多项|多选|多項|多選)')
_sec_re_3 = re.compile(r'三[、，.]\s*判[断斷]')

OUTPUT = r'D:\桌面\题库系统\questions.json'
MARKERS = ['【答案】', '【解析】', '【参考答案】', '【答案及解析】', '【知识点】']

# ── Helpers ────────────────────────────────────────────────

def clean_text(text):
    for pat in (_re_header1, _re_header2, _re_header3, _re_page):
        text = pat.sub('', text)
    text = _re_multi_nl.sub('\n\n', text)
    text = _re_multi_sp.sub(' ', text)
    text = _re_nl_sp.sub('\n', text)
    text = _re_standalone.sub('\n', text)
    return text.strip()


def extract_year(filename, full_text):
    m = _re_year.search(filename) or _re_year_cn.search(full_text[:500])
    return int(m.group(1)) if m else None


def norm_judgment(ans):
    ans = ans.strip()
    if ans in ('√', '对', '正确', 'V'): return '正确'
    if ans in ('×', '错', '错误', 'X', 'x'): return '错误'
    return ans


def cut_before_marker(text):
    """Return text up to first marker."""
    best = len(text)
    for m in MARKERS:
        idx = text.find(m)
        if idx >= 0 and idx < best:
            best = idx
    return text[:best].strip() if best < len(text) else text


def extract_answer(content):
    """Split-based answer extraction — no DOTALL."""
    ans, exp = '', ''

    if '【答案及解析】' in content:
        rest = content.split('【答案及解析】', 1)[1].strip()
        return rest, rest

    if '【参考答案】' in content:
        after = content.split('【参考答案】', 1)[1]
        ans = after.split('【解析】')[0].split('【知识点】')[0].strip()
        ans = ans.replace('\n', ' ').strip()

    if not ans and '【答案】' in content:
        after = content.split('【答案】', 1)[1]
        ans = after.split('【解析】')[0].split('【知识点】')[0].strip()
        ans = ans.replace('\n', ' ').strip()

    if '【解析】' in content:
        after = content.split('【解析】', 1)[1]
        exp = after.split('【知识点】')[0].strip()
        exp = exp.replace('\n', ' ').strip()

    return ans, exp


def split_sections(full_text, sec4_re, sec5_re):
    """Split PDF text into 5 sections."""
    sections = {1: '', 2: '', 3: '', 4: '', 5: ''}
    patterns = [
        (1, _sec_re_1), (2, _sec_re_2), (3, _sec_re_3),
        (4, sec4_re), (5, sec5_re),
    ]
    positions = []
    for sid, pat in patterns:
        m = pat.search(full_text)
        if m:
            positions.append((m.start(), sid))
    positions.sort()
    for i, (pos, sid) in enumerate(positions):
        end = positions[i+1][0] if i+1 < len(positions) else len(full_text)
        sections[sid] = full_text[pos:end]
    return sections


# ── Parsers ────────────────────────────────────────────────

def parse_choice(section_text, qtype):
    """Parse 单选/多选 — line-by-line, no DOTALL."""
    questions = []
    parts = _re_qnum.split('\n' + section_text)
    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2; continue
        content = parts[i+1] if i+1 < len(parts) else ''
        i += 2
        if not content.strip():
            continue

        # Line-by-line option extraction
        lines = content.split('\n')
        options = []
        qtext_end = 0
        found = False

        for li, line in enumerate(lines):
            m = _re_opt.match(line)
            if m:
                if not found:
                    found = True
                    qtext_end = li
                opt_text = cut_before_marker(m.group(2))
                options.append(opt_text)
            elif found and any(line.strip().startswith(m) for m in MARKERS):
                break

        qtext = '\n'.join(lines[:qtext_end]).strip() if found else content
        qtext = _re_multi_sp.sub(' ', qtext).strip()
        if not qtext or len(qtext) < 5:
            continue

        ans, exp = extract_answer(content)
        if qtype in ('单选题', '多选题'):
            ans = _re_answer_clean.sub('', ans).upper()
            ans = _re_answer_prefix.sub('', ans)

        questions.append({
            'qtype': qtype, 'number': num,
            'question': qtext, 'options': options,
            'answer': ans, 'explanation': exp,
        })
    return questions


def parse_judgment(section_text):
    """Parse 判断题."""
    questions = []
    parts = _re_qnum.split('\n' + section_text)
    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2; continue
        content = parts[i+1] if i+1 < len(parts) else ''
        i += 2
        if not content.strip():
            continue

        ans, exp = extract_answer(content)
        ans = norm_judgment(ans)

        qtext = cut_before_marker(content)
        qtext = _re_judge_at.sub('', qtext)
        qtext = _re_judge_bt.sub('', qtext)
        qtext = _re_multi_sp.sub(' ', qtext).strip()
        if not qtext or len(qtext) < 3:
            continue

        questions.append({
            'qtype': '判断题', 'number': num,
            'question': qtext, 'options': [],
            'answer': ans, 'explanation': exp,
        })
    return questions


def parse_text(section_text, qtype):
    """Parse 计算分析/简答/综合题."""
    questions = []
    parts = _re_qnum.split('\n' + section_text)
    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2; continue
        content = parts[i+1] if i+1 < len(parts) else ''
        i += 2
        if not content.strip():
            continue

        ans, exp = extract_answer(content)

        if not ans:
            m = _re_requirement.search(content)
            if m:
                req_parts = _re_requirement.split(content)
                if len(req_parts) >= 2:
                    qtext = req_parts[0].strip()
                    rest = '要求：'.join(req_parts[1:])
                    if '【答案】' in rest:
                        ans = rest.split('【答案】', 1)[1].strip()
                    elif '【参考答案】' in rest:
                        ans = rest.split('【参考答案】', 1)[1].strip()
                    elif '【解析】' in rest:
                        a, b = rest.split('【解析】', 1)
                        ans, exp = a.strip(), b.strip() if not exp else exp
                    else:
                        ans = rest
                if not ans:
                    ans = content[m.end():].strip()
            else:
                qtext = content
        else:
            qtext = cut_before_marker(content)

        qtext = _re_multi_sp.sub(' ', qtext).strip()
        ans = ans.strip()
        if not qtext or len(qtext) < 10:
            continue

        questions.append({
            'qtype': qtype, 'number': num,
            'question': qtext, 'options': [],
            'answer': ans, 'explanation': exp,
        })
    return questions


def parse_single_text(section_text, qtype):
    """Fallback: entire section as one question."""
    lines = section_text.strip().split('\n')
    content = '\n'.join(lines[1:]) if lines and _re_s45.search(lines[0]) else section_text
    content = content.strip()
    if not content or len(content) < 10:
        return []

    ans, exp = extract_answer(content)
    qtext = cut_before_marker(content)
    qtext = _re_multi_sp.sub(' ', qtext).strip()
    if not qtext or len(qtext) < 10:
        return []

    return [{'qtype': qtype, 'number': 1, 'question': qtext,
             'options': [], 'answer': ans, 'explanation': exp}]


def fill(questions, qtype, expected):
    existing = {q['number'] for q in questions}
    for n in range(1, expected + 1):
        if n not in existing:
            questions.append({
                'qtype': qtype, 'number': n,
                'question': '当前题目还未收集到',
                'options': [], 'answer': '', 'explanation': '',
            })
    questions.sort(key=lambda q: q['number'])
    return questions


# ── PDF parser ─────────────────────────────────────────────

def parse_pdf(filepath, cfg):
    filename = os.path.basename(filepath)
    print(f'  Reading: {filename}')

    doc = pymupdf.open(filepath)
    full_text = ''.join(page.get_text() for page in doc)
    doc.close()

    full_text = clean_text(full_text)
    year = extract_year(filename, full_text)
    print(f'    Year: {year}')

    sections = split_sections(full_text, cfg['sec4_re'], cfg['sec5_re'])
    all_qs = []
    ec = cfg['counts']
    do_fill = cfg['fill']
    s4type = cfg['sec4_type']

    def process(sec_id, qtype, parser, exp_key):
        nonlocal all_qs
        if sections[sec_id]:
            qs = parser(sections[sec_id], qtype)
            for j, q in enumerate(qs):
                q['number'] = j + 1
            if do_fill and exp_key in ec:
                qs = fill(qs, qtype, ec[exp_key])
                qs = qs[:ec[exp_key]]
            print(f'    {qtype}: {len(qs)} (expected {ec.get(exp_key, "?")})')
            all_qs.extend(qs)
        else:
            print(f'    {qtype} section NOT found')
            if do_fill and exp_key in ec:
                all_qs.extend(fill([], qtype, ec[exp_key]))

    process(1, '单选题', parse_choice, '单选题')
    process(2, '多选题', parse_choice, '多选题')
    process(3, '判断题', lambda t, qt: parse_judgment(t), '判断题')
    process(4, s4type, parse_text, s4type)

    # Section 5 综合题 with fallback
    if sections[5]:
        qs = parse_text(sections[5], '综合题') or parse_single_text(sections[5], '综合题')
        for j, q in enumerate(qs):
            q['number'] = j + 1
        if do_fill and '综合题' in ec:
            qs = fill(qs, '综合题', ec['综合题'])
            qs = qs[:ec['综合题']]
        print(f'    综合题: {len(qs)} (expected {ec.get("综合题", "?")})')
        all_qs.extend(qs)
    elif do_fill and '综合题' in ec:
        print('    综合题 section NOT found')
        all_qs.extend(fill([], '综合题', ec['综合题']))

    for q in all_qs:
        q['year'] = year
        q['source'] = filename
    return all_qs


# ── Main ───────────────────────────────────────────────────

def main():
    print('Loading questions.json...')
    with open(OUTPUT, 'r', encoding='utf-8') as f:
        existing = json.load(f)
    print(f'  {len(existing)} questions loaded')

    lookup = {}
    for idx, q in enumerate(existing):
        lookup[(q.get('year'), q.get('type'), q.get('number'), q.get('category'))] = idx

    max_id = max((q['id'] for q in existing), default=0)
    nid = max_id + 1
    tot_add, tot_upd, tot_skip = 0, 0, 0

    for cfg in SUBJECTS:
        d = cfg['pdf_dir']
        cat = cfg['category']
        if not os.path.isdir(d):
            print(f'\nSKIP: {d} not found'); continue

        pdfs = sorted(f for f in os.listdir(d) if f.lower().endswith('.pdf'))
        print(f'\n{"="*50}\n{cat} ({len(pdfs)} PDFs)\n{"-"*50}')

        extracted = []
        for p in pdfs:
            extracted.extend(parse_pdf(os.path.join(d, p), cfg))
            print()

        print(f'  Extracted: {len(extracted)}')

        add, upd, skip = 0, 0, 0
        for q in extracted:
            key = (q['year'], q['qtype'], q['number'], cat)
            if key in lookup:
                ei = lookup[key]
                eq = existing[ei]
                if eq.get('has_answer') and eq.get('answer'):
                    skip += 1; continue
                if not q.get('answer') and eq.get('answer'):
                    skip += 1; continue
                eq['answer'] = q['answer']
                eq['explanation'] = q['explanation']
                if q.get('answer'):
                    eq['has_answer'] = True
                eq['options'] = q['options']
                eq['question'] = q['question']
                eq['source'] = q['source']
                upd += 1
            else:
                existing.append({
                    'id': nid, 'subject': '中级会计考试', 'level': '中级',
                    'year': q['year'], 'type': q['qtype'], 'number': q['number'],
                    'question': q['question'], 'options': q['options'],
                    'answer': q['answer'], 'explanation': q['explanation'],
                    'has_answer': bool(q.get('answer')),
                    'source': q['source'], 'category': cat,
                })
                lookup[key] = len(existing) - 1
                nid += 1
                add += 1

        print(f'  +{add} ~{upd} -{skip}')
        tot_add += add; tot_upd += upd; tot_skip += skip

    print(f'\n{"="*50}\nTOTAL: +{tot_add} ~{tot_upd} -{tot_skip}')
    print(f'Writing {len(existing)} questions...')
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print('Done!')


if __name__ == '__main__':
    main()
