def find_factors(product):
    if product <= 0:
        return []
    answers = []
    i = 1
    while i * i <= product:
        if product % i == 0:
            answers.append((i, product // i))
        i += 1
    for j in range(len(answers) - 1, -1, -1):
        a, b = answers[j]
        if a != b:
            answers.append((b, a))
    return answers
