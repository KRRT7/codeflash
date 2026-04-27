def matrix_multiply(A, B):
    rows_A = len(A)
    cols_A = len(A[0])
    rows_B = len(B)
    cols_B = len(B[0])

    if cols_A != rows_B:
        raise ValueError("Matrices A and B cannot be multiplied")

    result = [[0 for _ in range(cols_B)] for _ in range(rows_A)]

    for i in range(rows_A):
        A_i = A[i]
        result_i = result[i]
        for j in range(cols_B):
            sum_val = 0
            for k in range(rows_B):
                sum_val += A_i[k] * B[k][j]
            result_i[j] = sum_val
    return result
