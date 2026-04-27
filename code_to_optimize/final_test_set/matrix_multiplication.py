def matrix_multiply(A, B):
    n_rows_A = len(A)
    n_cols_A = len(A[0])
    n_rows_B = len(B)
    n_cols_B = len(B[0])

    if n_cols_A != n_rows_B:
        raise ValueError("Matrices A and B cannot be multiplied")

    result = [[0 for _ in range(n_cols_B)] for _ in range(n_rows_A)]

    for i in range(n_rows_A):
        row_A = A[i]
        result_row = result[i]
        for j in range(n_cols_B):
            total = 0
            for k in range(n_rows_B):
                total += row_A[k] * B[k][j]
            result_row[j] = total
    return result
