#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Extract questions from 中级会计实务 PDFs into JSON format for the question bank system."""

import pymupdf
import json
import re
import os
import sys

PDF_DIR = r"D:\桌面\RAG数据库\中级会计实务"
OUTPUT_FILE = r"D:\桌面\题库系统\questions.json"
CATEGORY = "中级会计实务"

EXPECTED_COUNTS = {
    '单选题': 10,
    '多选题': 10,
    '判断题': 10,
    '计算分析题': 2,
    '综合题': 2,
}

# ── helpers ──────────────────────────────────────────────

def clean_text(text):
    """Remove page artifacts, normalize whitespace."""
    text = re.sub(r'学员专用资料.*?第\d+页/共\d+页', '', text)
    text = re.sub(r'环球网校学员专用', '', text)
    text = re.sub(r'课程咨询：\d*', '', text)
    text = re.sub(r'第\s*\d+\s*页\s*/\s*共\s*\d+\s*页', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n +', '\n', text)
    text = re.sub(r'\n\d+\n', '\n', text)
    return text.strip()


def extract_year(filename, full_text):
    """Extract year from filename or first page."""
    m = re.search(r'(20\d{2})', filename)
    if m:
        return int(m.group(1))
    m = re.search(r'(20\d{2})年', full_text[:500])
    if m:
        return int(m.group(1))
    return None


def normalize_judgment_answer(ans):
    """Normalize judgment answers to 正确/错误."""
    ans = ans.strip()
    if ans in ('√', '对', '正确', 'V'):
        return '正确'
    if ans in ('×', '错', '错误', 'X', 'x'):
        return '错误'
    return ans


def extract_answer(text_after_question):
    """Extract answer from 【答案】/【参考答案】/【答案及解析】 markers. Returns (answer, explanation)."""
    answer = ''
    explanation = ''

    # Try 【答案及解析】 (combined marker) first
    ans_match = re.search(r'【答案及解析】?\s*(.+?)$', text_after_question, re.DOTALL)
    if ans_match:
        answer = ans_match.group(1).strip()
        explanation = answer
        return answer, explanation

    # Try 【参考答案】
    ans_match = re.search(r'【参考答案】?\s*(.+?)(?:【解析】|【知识点】|$)', text_after_question, re.DOTALL)
    if ans_match:
        answer = ans_match.group(1).strip()
        answer = re.sub(r'\n+', '\n', answer).strip()

    # Fall back to 【答案】
    if not answer:
        ans_match = re.search(r'【答案】?\s*(.+?)(?:【解析】|【知识点】|$)', text_after_question, re.DOTALL)
        if ans_match:
            answer = ans_match.group(1).strip()
            answer = re.sub(r'\n+', '\n', answer).strip()

    exp_match = re.search(r'【解析】?\s*(.+?)$', text_after_question, re.DOTALL)
    if exp_match:
        explanation = exp_match.group(1).strip()
        explanation = re.sub(r'\n+', '\n', explanation).strip()

    return answer, explanation


def split_sections(full_text):
    """Split PDF text into 5 sections based on headers."""
    sections = {1: '', 2: '', 3: '', 4: '', 5: ''}

    patterns = [
        (1, r'一[、，.]\s*(?:单项|单选|單項|單選)'),
        (2, r'二[、，.]\s*(?:多项|多选|多項|多選)'),
        (3, r'三[、，.]\s*判[断斷]'),
        (4, r'四[、，.]\s*(?:计算|計算)'),
        (5, r'五[、，.]\s*(?:综合|綜合)'),
    ]

    positions = []
    for sec_id, pat in patterns:
        m = re.search(pat, full_text)
        if m:
            positions.append((m.start(), sec_id, m.group()))

    positions.sort()

    for i, (pos, sec_id, header) in enumerate(positions):
        if i + 1 < len(positions):
            end_pos = positions[i + 1][0]
        else:
            end_pos = len(full_text)
        sections[sec_id] = full_text[pos:end_pos]

    return sections


def parse_choice_questions(section_text, qtype):
    """Parse 单选题 or 多选题 from a section. Returns list of question dicts."""
    questions = []
    parts = re.split(r'\n(\d{1,2})[.、．]\s*', '\n' + section_text)

    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2
            continue
        content = parts[i + 1] if i + 1 < len(parts) else ''
        i += 2

        if not content.strip():
            continue

        options_raw = re.findall(r'([A-E])[.、．]\s*(.+?)(?=\n[A-E][.、．]|\n【答案】|\n【解析】|\n【参考答案】|\n【答案及解析】|\n【知识点】|$)', content, re.DOTALL)
        options = []
        for letter, opt_text in options_raw:
            opt_text = re.sub(r'\s+', ' ', opt_text).strip()
            opt_text = re.sub(r'【答案.*$', '', opt_text).strip()
            opt_text = re.sub(r'【解析】.*$', '', opt_text).strip()
            options.append(opt_text)

        qtext = content
        if options_raw:
            first_opt_pos = content.find(options_raw[0][0] + '.')
            if first_opt_pos < 0:
                first_opt_pos = content.find(options_raw[0][0] + '、')
            if first_opt_pos > 0:
                qtext = content[:first_opt_pos].strip()

        qtext = re.sub(r'\s+', ' ', qtext).strip()
        if not qtext or len(qtext) < 5:
            continue

        answer, explanation = extract_answer(content)

        if qtype in ('单选题', '多选题'):
            answer = re.sub(r'[^A-Ea-e]', '', answer).upper()
            answer = re.sub(r'选项[A-E]', '', answer)

        questions.append({
            'qtype': qtype,
            'number': num,
            'question': qtext,
            'options': options,
            'answer': answer,
            'explanation': explanation,
        })

    return questions


def parse_judgment_questions(section_text):
    """Parse 判断题 from a section."""
    questions = []
    parts = re.split(r'\n(\d{1,2})[.、．]\s*', '\n' + section_text)

    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2
            continue
        content = parts[i + 1] if i + 1 < len(parts) else ''
        i += 2

        if not content.strip():
            continue

        answer, explanation = extract_answer(content)
        answer = normalize_judgment_answer(answer)

        qtext = re.split(r'【答案】|【解析】|【参考答案】|【答案及解析】|【知识点】', content)[0].strip()
        qtext = re.sub(r'\nA[.、．]\s*对\b.*', '', qtext)
        qtext = re.sub(r'\nA[.、．]\s*正确\b.*', '', qtext)
        qtext = re.sub(r'\nB[.、．]\s*错\b.*', '', qtext)
        qtext = re.sub(r'\nB[.、．]\s*错误\b.*', '', qtext)
        qtext = re.sub(r'\s+', ' ', qtext).strip()

        if not qtext or len(qtext) < 3:
            continue

        questions.append({
            'qtype': '判断题',
            'number': num,
            'question': qtext,
            'options': [],
            'answer': answer,
            'explanation': explanation,
        })

    return questions


def parse_text_questions(section_text, qtype):
    """Parse 计算分析题 or 综合题 (text-input types) from a section."""
    questions = []
    parts = re.split(r'\n(\d{1,2})[.、．]\s*', '\n' + section_text)

    i = 1
    while i < len(parts) - 1:
        try:
            num = int(parts[i])
        except ValueError:
            i += 2
            continue
        content = parts[i + 1] if i + 1 < len(parts) else ''
        i += 2

        if not content.strip():
            continue

        answer, explanation = extract_answer(content)

        if not answer:
            req_match = re.search(r'要求[：:]\s*', content)
            if req_match:
                req_parts = re.split(r'要求[：:]\s*', content)
                if len(req_parts) >= 2:
                    qtext = req_parts[0].strip()
                    remaining = '要求：'.join(req_parts[1:])
                    ans_match2 = re.search(r'【答案】?\s*(.+)', remaining, re.DOTALL)
                    if not ans_match2:
                        ans_match2 = re.search(r'【参考答案】?\s*(.+)', remaining, re.DOTALL)
                    if ans_match2:
                        answer = ans_match2.group(1).strip()
                    else:
                        exp_match2 = re.search(r'【解析】?\s*(.+)', remaining, re.DOTALL)
                        if exp_match2:
                            answer = remaining[:exp_match2.start()].strip()
                            if not explanation:
                                explanation = exp_match2.group(1).strip()
                        else:
                            answer = remaining
                if not answer:
                    answer = content[req_match.end():].strip()
            else:
                qtext = content
        else:
            qtext = re.split(r'【答案】|【解析】|【参考答案】|【答案及解析】|【知识点】', content)[0].strip()

        qtext = re.sub(r'\s+', ' ', qtext).strip()
        answer = answer.strip()

        if not qtext or len(qtext) < 10:
            continue

        questions.append({
            'qtype': qtype,
            'number': num,
            'question': qtext,
            'options': [],
            'answer': answer,
            'explanation': explanation,
        })

    return questions


def parse_single_text_question(section_text, qtype):
    """Parse an entire section as a single text question (fallback for unnumbered sections)."""
    lines = section_text.strip().split('\n')
    if lines and re.search(r'[四五][、，.]', lines[0]):
        content = '\n'.join(lines[1:]).strip()
    else:
        content = section_text.strip()

    if not content or len(content) < 10:
        return []

    answer, explanation = extract_answer(content)
    qtext = re.split(r'【答案】|【解析】|【参考答案】|【答案及解析】|【知识点】', content)[0].strip()
    qtext = re.sub(r'\s+', ' ', qtext).strip()

    if not qtext or len(qtext) < 10:
        return []

    return [{
        'qtype': qtype,
        'number': 1,
        'question': qtext,
        'options': [],
        'answer': answer,
        'explanation': explanation,
    }]


def fill_missing(questions, qtype, expected_count, year, filename):
    """Fill in missing question numbers with placeholders."""
    existing_numbers = {q['number'] for q in questions}
    for num in range(1, expected_count + 1):
        if num not in existing_numbers:
            questions.append({
                'qtype': qtype,
                'number': num,
                'question': '当前题目还未收集到',
                'options': [],
                'answer': '',
                'explanation': '',
            })
    questions.sort(key=lambda q: q['number'])
    return questions


def parse_pdf(filepath):
    """Parse a single PDF, return list of question dicts."""
    filename = os.path.basename(filepath)
    print(f'  Reading: {filename}')

    doc = pymupdf.open(filepath)
    full_text = ''
    for page in doc:
        full_text += page.get_text()
    doc.close()

    full_text = clean_text(full_text)
    year = extract_year(filename, full_text)
    print(f'    Year: {year}')

    sections = split_sections(full_text)
    all_questions = []

    # Section 1: 单选题
    if sections[1]:
        qs = parse_choice_questions(sections[1], '单选题')
        for i, q in enumerate(qs):
            q['number'] = i + 1
        qs = fill_missing(qs, '单选题', EXPECTED_COUNTS['单选题'], year, filename)
        qs = qs[:EXPECTED_COUNTS['单选题']]
        print(f'    单选题: {len(qs)} (expected {EXPECTED_COUNTS["单选题"]})')
        all_questions.extend(qs)
    else:
        print('    单选题 section NOT found — filling all as missing')
        all_questions.extend(fill_missing([], '单选题', EXPECTED_COUNTS['单选题'], year, filename))

    # Section 2: 多选题
    if sections[2]:
        qs = parse_choice_questions(sections[2], '多选题')
        for i, q in enumerate(qs):
            q['number'] = i + 1
        qs = fill_missing(qs, '多选题', EXPECTED_COUNTS['多选题'], year, filename)
        qs = qs[:EXPECTED_COUNTS['多选题']]
        print(f'    多选题: {len(qs)} (expected {EXPECTED_COUNTS["多选题"]})')
        all_questions.extend(qs)
    else:
        print('    多选题 section NOT found — filling all as missing')
        all_questions.extend(fill_missing([], '多选题', EXPECTED_COUNTS['多选题'], year, filename))

    # Section 3: 判断题
    if sections[3]:
        qs = parse_judgment_questions(sections[3])
        for i, q in enumerate(qs):
            q['number'] = i + 1
        qs = fill_missing(qs, '判断题', EXPECTED_COUNTS['判断题'], year, filename)
        qs = qs[:EXPECTED_COUNTS['判断题']]
        print(f'    判断题: {len(qs)} (expected {EXPECTED_COUNTS["判断题"]})')
        all_questions.extend(qs)
    else:
        print('    判断题 section NOT found — filling all as missing')
        all_questions.extend(fill_missing([], '判断题', EXPECTED_COUNTS['判断题'], year, filename))

    # Section 4: 计算分析题
    if sections[4]:
        qs = parse_text_questions(sections[4], '计算分析题')
        if len(qs) == 0:
            qs = parse_single_text_question(sections[4], '计算分析题')
        for i, q in enumerate(qs):
            q['number'] = i + 1
        qs = fill_missing(qs, '计算分析题', EXPECTED_COUNTS['计算分析题'], year, filename)
        qs = qs[:EXPECTED_COUNTS['计算分析题']]
        print(f'    计算分析题: {len(qs)} (expected {EXPECTED_COUNTS["计算分析题"]})')
        all_questions.extend(qs)
    else:
        print('    计算分析题 section NOT found — filling all as missing')
        all_questions.extend(fill_missing([], '计算分析题', EXPECTED_COUNTS['计算分析题'], year, filename))

    # Section 5: 综合题 (with fallback for unnumbered sections)
    if sections[5]:
        qs = parse_text_questions(sections[5], '综合题')
        if len(qs) == 0:
            qs = parse_single_text_question(sections[5], '综合题')
        for i, q in enumerate(qs):
            q['number'] = i + 1
        qs = fill_missing(qs, '综合题', EXPECTED_COUNTS['综合题'], year, filename)
        qs = qs[:EXPECTED_COUNTS['综合题']]
        print(f'    综合题: {len(qs)} (expected {EXPECTED_COUNTS["综合题"]})')
        all_questions.extend(qs)
    else:
        print('    综合题 section NOT found — filling all as missing')
        all_questions.extend(fill_missing([], '综合题', EXPECTED_COUNTS['综合题'], year, filename))

    for q in all_questions:
        q['year'] = year
        q['source'] = filename

    return all_questions


def main():
    pdf_files = sorted([
        os.path.join(PDF_DIR, f) for f in os.listdir(PDF_DIR)
        if f.lower().endswith('.pdf')
    ])
    print(f'Found {len(pdf_files)} PDF files\n')

    all_extracted = []
    for pdf_path in pdf_files:
        qs = parse_pdf(pdf_path)
        all_extracted.extend(qs)
        print()

    print(f'Total extracted: {len(all_extracted)} questions')

    print(f'\nLoading existing questions from: {OUTPUT_FILE}')
    with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
        existing = json.load(f)
    print(f'Existing questions: {len(existing)}')

    existing_lookup = {}
    for idx, q in enumerate(existing):
        key = (q.get('year'), q.get('type'), q.get('number'), q.get('category'))
        existing_lookup[key] = idx

    max_id = max((q['id'] for q in existing), default=0)
    next_id = max_id + 1

    added = 0
    updated = 0
    skipped = 0
    for q in all_extracted:
        key = (q['year'], q['qtype'], q['number'], CATEGORY)
        if key in existing_lookup:
            idx = existing_lookup[key]
            existing_q = existing[idx]
            if existing_q.get('has_answer') and existing_q.get('answer'):
                skipped += 1
                continue
            if not q.get('answer') and existing_q.get('answer'):
                skipped += 1
                continue
            existing_q['answer'] = q['answer']
            existing_q['explanation'] = q['explanation']
            if q.get('answer'):
                existing_q['has_answer'] = True
            existing_q['options'] = q['options']
            existing_q['question'] = q['question']
            existing_q['source'] = q['source']
            updated += 1
        else:
            has_answer = bool(q.get('answer'))
            new_entry = {
                'id': next_id,
                'subject': '中级会计考试',
                'level': '中级',
                'year': q['year'],
                'type': q['qtype'],
                'number': q['number'],
                'question': q['question'],
                'options': q['options'],
                'answer': q['answer'],
                'explanation': q['explanation'],
                'has_answer': has_answer,
                'source': q['source'],
                'category': CATEGORY,
            }
            existing.append(new_entry)
            next_id += 1
            added += 1

    print(f'\nMerge results:')
    print(f'  Added: {added}')
    print(f'  Updated: {updated}')
    print(f'  Skipped (duplicate): {skipped}')
    print(f'  New max ID: {next_id - 1}')

    print(f'\nWriting to: {OUTPUT_FILE}')
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print('Done!')

    type_counts = {}
    for q in all_extracted:
        t = q['qtype']
        type_counts[t] = type_counts.get(t, 0) + 1
    print('\nExtracted by type:')
    for t in ['单选题', '多选题', '判断题', '计算分析题', '综合题']:
        print(f'  {t}: {type_counts.get(t, 0)}')


if __name__ == '__main__':
    main()
