def find_duplicates(lst):
    seen = {}
    duplicates = []
    duplicates_set = set()
    for idx, item in enumerate(lst):
        if item in seen:
            if item not in duplicates_set:
                duplicates.append((seen[item], item))
                duplicates_set.add(item)
        else:
            seen[item] = idx
    duplicates.sort()
    return [item for _, item in duplicates]
