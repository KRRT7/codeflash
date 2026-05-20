from __future__ import annotations

import ast


def create_device_sync_precompute_statements(
    used_frameworks: dict[str, str] | None,
) -> list[ast.stmt]:
    """Create AST statements to pre-compute device sync conditions before profiling.

    This moves the conditional checks (like is_available(), hasattr(), etc.) outside
    the timing block to avoid their overhead affecting the measurements.

    Args:
        used_frameworks: Dict mapping framework names to their import aliases

    Returns:
        List of AST statements that pre-compute sync conditions into boolean variables

    """
    if not used_frameworks:
        return []

    precompute_statements: list[ast.stmt] = []

    # PyTorch: pre-compute whether to sync CUDA or MPS
    if "torch" in used_frameworks:
        torch_alias = used_frameworks["torch"]
        # _codeflash_should_sync_cuda = torch.cuda.is_available() and torch.cuda.is_initialized()
        precompute_statements.append(
            ast.Assign(
                targets=[ast.Name(id="_codeflash_should_sync_cuda", ctx=ast.Store())],
                value=ast.BoolOp(
                    op=ast.And(),
                    values=[
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Attribute(
                                    value=ast.Name(id=torch_alias, ctx=ast.Load()),
                                    attr="cuda",
                                    ctx=ast.Load(),
                                ),
                                attr="is_available",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Attribute(
                                    value=ast.Name(id=torch_alias, ctx=ast.Load()),
                                    attr="cuda",
                                    ctx=ast.Load(),
                                ),
                                attr="is_initialized",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                    ],
                ),
                lineno=1,
            )
        )
        # _codeflash_should_sync_mps = (not _codeflash_should_sync_cuda and
        #     hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() and
        #     hasattr(torch.mps, 'synchronize'))
        precompute_statements.append(
            ast.Assign(
                targets=[ast.Name(id="_codeflash_should_sync_mps", ctx=ast.Store())],
                value=ast.BoolOp(
                    op=ast.And(),
                    values=[
                        ast.UnaryOp(
                            op=ast.Not(),
                            operand=ast.Name(
                                id="_codeflash_should_sync_cuda", ctx=ast.Load()
                            ),
                        ),
                        ast.Call(
                            func=ast.Name(id="hasattr", ctx=ast.Load()),
                            args=[
                                ast.Attribute(
                                    value=ast.Name(id=torch_alias, ctx=ast.Load()),
                                    attr="backends",
                                    ctx=ast.Load(),
                                ),
                                ast.Constant(value="mps"),
                            ],
                            keywords=[],
                        ),
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Attribute(
                                    value=ast.Attribute(
                                        value=ast.Name(id=torch_alias, ctx=ast.Load()),
                                        attr="backends",
                                        ctx=ast.Load(),
                                    ),
                                    attr="mps",
                                    ctx=ast.Load(),
                                ),
                                attr="is_available",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                        ast.Call(
                            func=ast.Name(id="hasattr", ctx=ast.Load()),
                            args=[
                                ast.Attribute(
                                    value=ast.Name(id=torch_alias, ctx=ast.Load()),
                                    attr="mps",
                                    ctx=ast.Load(),
                                ),
                                ast.Constant(value="synchronize"),
                            ],
                            keywords=[],
                        ),
                    ],
                ),
                lineno=1,
            )
        )

    # JAX: pre-compute whether jax.block_until_ready exists
    if "jax" in used_frameworks:
        jax_alias = used_frameworks["jax"]
        # _codeflash_should_sync_jax = hasattr(jax, 'block_until_ready')
        precompute_statements.append(
            ast.Assign(
                targets=[ast.Name(id="_codeflash_should_sync_jax", ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Name(id="hasattr", ctx=ast.Load()),
                    args=[
                        ast.Name(id=jax_alias, ctx=ast.Load()),
                        ast.Constant(value="block_until_ready"),
                    ],
                    keywords=[],
                ),
                lineno=1,
            )
        )

    # TensorFlow: pre-compute whether tf.test.experimental.sync_devices exists
    if "tensorflow" in used_frameworks:
        tf_alias = used_frameworks["tensorflow"]
        # _codeflash_should_sync_tf = hasattr(tf.test.experimental, 'sync_devices')
        precompute_statements.append(
            ast.Assign(
                targets=[ast.Name(id="_codeflash_should_sync_tf", ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Name(id="hasattr", ctx=ast.Load()),
                    args=[
                        ast.Attribute(
                            value=ast.Attribute(
                                value=ast.Name(id=tf_alias, ctx=ast.Load()),
                                attr="test",
                                ctx=ast.Load(),
                            ),
                            attr="experimental",
                            ctx=ast.Load(),
                        ),
                        ast.Constant(value="sync_devices"),
                    ],
                    keywords=[],
                ),
                lineno=1,
            )
        )

    return precompute_statements


