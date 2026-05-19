# [Codeflash](https://www.codeflash.ai) is a general purpose optimizer for Python that helps you improve the performance of your Python code while maintaining its correctness

It uses advanced LLMs to generate multiple optimization ideas for your code, tests them to be correct and benchmarks them for performance. It then creates merge-ready pull requests containing the best optimization found, which you can review and merge.

How to use Codeflash -

- Optimize an entire existing codebase by running `codeflash --all`
- Automate optimizing all __future__ code you will write by installing Codeflash as a GitHub action.
- Optimize a Python workflow `python myscript.py` end-to-end by running `codeflash optimize myscript.py`

Codeflash is great at optimizing AI Agents, Computer Vision algorithms, PyTorch code, numerical code, backend code or anything else you might write with Python.
