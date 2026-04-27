def translate(word):
    vowels = "aeiou"
    if word[0] in vowels:
        return word + "way"
    else:
        consonant_count = 0
        for letter in word:
            if letter not in vowels:
                consonant_count += 1
            else:
                break
        return word[consonant_count:] + word[:consonant_count] + "ay"


def pig_latin(text):
    words = text.lower().split()
    translated_words = [translate(word) for word in words]
    return " ".join(translated_words)
