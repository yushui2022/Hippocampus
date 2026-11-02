import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hippocampus_napcat.tools import WebTools, plain


def test_safe_url_blocks_local():
    assert not WebTools.safe_url("http://127.0.0.1/x")
    assert not WebTools.safe_url("http://localhost/x")
    assert not WebTools.safe_url("file:///etc/passwd")
    assert not WebTools.safe_url("http://10.0.0.1/x")


def test_plain_strips_tags():
    assert plain("<b>你好</b><script>x</script> 世界") == "你好 世界"
