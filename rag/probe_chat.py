from rag.chat import answer
for q in ['Châm ngôn "tránh xa thế dày" nghĩa là gì?', 'Chấp 9 quân thì Đen nên chơi như thế nào?',
          'Tam giác ngu là gì, vì sao là hình xấu?']:
    r = answer('probe', q)
    print('\n#', q, '| EN:', r['search_en'], '| còn', r['remaining_today'])
    print('  nguồn:', [(s['book_id'], s['page']) for s in r['sources']])
    print(r['answer'][:900])
