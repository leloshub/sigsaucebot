with open('ssb.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('\u201c', '"')
content = content.replace('\u201d', '"')
content = content.replace('\u2018', "'")
content = content.replace('\u2019', "'")
content = content.replace('\u2014', '-')
content = content.replace('\u2013', '-')

with open('ssb.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Quotes fixed successfully')
