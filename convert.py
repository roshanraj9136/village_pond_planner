import markdown
with open('ASSIGNMENT_PHASE_2_REPORT.md', 'r', encoding='utf-8') as f:
    text = f.read()
html = markdown.markdown(text, extensions=['tables', 'fenced_code'])
with open('report.html', 'w', encoding='utf-8') as f:
    f.write('<html><head><style>body{font-family: Arial, sans-serif; line-height: 1.6; margin: 40px; max-width: 800px;} table {border-collapse: collapse; width: 100%;} th, td {border: 1px solid #ddd; padding: 8px;} th {background-color: #f2f2f2;}</style></head><body>')
    f.write(html)
    f.write('</body></html>')
