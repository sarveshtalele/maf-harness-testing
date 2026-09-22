"""Static gates for generated-test mistakes that used to surface only at execution (found on personal-finance-using-RAG).

Every BAD sample below is (a reduction of) a test the model really generated and that failed at run time; each GOOD sample is
the corrected form and must pass, so the gates do not become a new source of repair rounds.
"""

from __future__ import annotations

import json

import pytest

import maf_test_harness.workflow as wf
from maf_test_harness.envs import env_check
from maf_test_harness.errors import classify
from maf_test_harness.qa import triage
from maf_test_harness.tools.code_intel import RepoContext, static_gate

MAIN = """import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.data_ingestion import load_pdf
from app.rag_pipeline import get_chain

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_pdf("data/x.pdf")
    app.state.chain = get_chain()
    yield

app = FastAPI(lifespan=lifespan)
"""
INGEST = """import os
from langchain_community.llms import Ollama

def load_pdf(pdf_path: str):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError("Unable to load PDF file!")
    return open(pdf_path, "rb").read()
"""
RAG = """from langchain.chains import RetrievalQA
from langchain_community.llms import Ollama
from langchain_community.vectorstores import Chroma

def get_chain():
    llm = Ollama(model="phi3")
    return RetrievalQA.from_chain_type(llm=llm, retriever=None)
"""
SOURCES = {
    "app/main.py": MAIN,
    "app/data_ingestion.py": INGEST,
    "app/rag_pipeline.py": RAG,
    "app/__init__.py": "",
}


@pytest.fixture
def ctx(tmp_path):
    for rel, text in SOURCES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "fastapi\nstreamlit\nlangchain\nlangchain-core\nlangchain-community\npytest-mock\nplaywright\n"
        "pytest-asyncio\nhttpx\n",
        encoding="utf-8",
    )
    return RepoContext(root=tmp_path, source_files=list(SOURCES), package_files=["requirements.txt"])


def gate(ctx, code):
    static_gate(ctx, "tests/test_x.py", code)


# --- @asynccontextmanager driven as a generator -----------------------------------------------------------------

LIFESPAN_BAD = """
import pytest
from unittest.mock import MagicMock, patch
from app.main import lifespan

@pytest.mark.asyncio
async def test_lifespan():
    app = MagicMock()
    with patch("app.main.load_pdf"), patch("app.main.get_chain"):
        gen = lifespan(app)
        await gen.__anext__()
"""
LIFESPAN_GOOD = """
import pytest
from unittest.mock import MagicMock, patch
from app.main import lifespan

@pytest.mark.asyncio
async def test_lifespan():
    app = MagicMock()
    with patch("app.main.load_pdf") as load, patch("app.main.get_chain"):
        async with lifespan(app):
            pass
    load.assert_called_once()
"""


def test_a_context_manager_driven_like_a_generator_is_rejected_with_the_fix(ctx):
    with pytest.raises(ValueError, match=r"decorated with @asynccontextmanager.*async with lifespan"):
        gate(ctx, LIFESPAN_BAD)
    with pytest.raises(ValueError, match="__anext__"):
        gate(ctx, "from app.main import lifespan\nasync def t():\n    await lifespan(None).__anext__()\n")
    with pytest.raises(ValueError, match="anext"):
        gate(
            ctx, "from app.main import lifespan\nasync def t():\n    g = lifespan(None)\n    await anext(g)\n"
        )


def test_using_it_as_a_context_manager_passes(ctx):
    gate(ctx, LIFESPAN_GOOD)


# --- autospec on app.state --------------------------------------------------------------------------------------------

STATE_BAD = """
from fastapi.testclient import TestClient
from app.main import app

def test_x(mocker):
    mocker.patch.object(app.state, "chain", autospec=True)
    assert TestClient(app)
"""
STATE_GOOD = """
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.main import app

def test_x():
    app.state.chain = MagicMock()
    assert TestClient(app)
"""


def test_autospec_on_app_state_is_rejected_and_a_plain_assignment_passes(ctx):
    with pytest.raises(ValueError, match=r"autospec=True.*app\.state\.chain = MagicMock\(\)"):
        gate(ctx, STATE_BAD)
    gate(ctx, STATE_GOOD)
    gate(
        ctx,
        "from unittest.mock import patch\nimport app.main as m\n\ndef test_x():\n    with patch.object(m, 'PDF_PATH', autospec=True):\n        pass\n",
    )


# --- MagicMock as a LangChain LLM -------------------------------------------------------------------------------------

LLM_BAD = """
from app.rag_pipeline import get_chain

def test_x(mocker):
    mocker.patch("app.rag_pipeline.Ollama")
    mocker.patch("app.rag_pipeline.Chroma")
    assert get_chain()
"""
LLM_GOOD = """
from langchain_core.language_models.fake import FakeListLLM
from app.rag_pipeline import get_chain

def test_x(mocker):
    mocker.patch("app.rag_pipeline.Ollama", return_value=FakeListLLM(responses=["ok"]))
    assert get_chain()
"""


def test_a_magicmock_llm_inside_a_real_chain_is_rejected_a_fake_llm_passes(ctx):
    with pytest.raises(ValueError, match=r"FakeListLLM.*Runnable|Runnable.*FakeListLLM"):
        gate(ctx, LLM_BAD)
    gate(ctx, LLM_GOOD)
    # a test that patches the chain builder too never gives the mock to a real chain (a real passing test of the run)
    both = (
        "def test_x(mocker):\n"
        '    mocker.patch("app.rag_pipeline.RetrievalQA")\n'
        '    mocker.patch("app.rag_pipeline.Ollama")\n'
        "    assert True\n"
    )
    gate(ctx, both)
    # patching the LLM name in a module that builds no chain is harmless
    gate(ctx, "def test_x(mocker):\n    mocker.patch('app.data_ingestion.Ollama')\n    assert True\n")


# --- Streamlit HTML ---------------------------------------------------------------------------------------------------

STREAMLIT_BAD = """
import subprocess, sys, urllib.request

def test_sidebar():
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless", "true"])
    try:
        html = urllib.request.urlopen("http://127.0.0.1:8591").read().decode()
        assert "Table of Contents" in html
    finally:
        proc.terminate()
"""
STREAMLIT_GOOD = """
from streamlit.testing.v1 import AppTest

def test_sidebar():
    at = AppTest.from_file("streamlit_app.py").run()
    assert "Table of Contents" in at.sidebar.radio[0].options
"""


def test_reading_streamlit_html_is_rejected_apptest_passes(ctx):
    with pytest.raises(ValueError, match=r"empty JavaScript shell.*AppTest"):
        gate(ctx, STREAMLIT_BAD)
    gate(ctx, STREAMLIT_GOOD)
    gate(
        ctx,
        STREAMLIT_BAD.replace(
            "import subprocess", "from playwright.sync_api import sync_playwright\nimport subprocess"
        ),
    )


# --- existence guard --------------------------------------------------------------------------------------------------

GUARD_BAD = """
from unittest.mock import patch
from app.data_ingestion import load_pdf

@patch("app.data_ingestion.open")
def test_load(m):
    load_pdf("/tmp/test_valid.pdf")
"""
GUARD_VARIABLE = """
from unittest.mock import patch
from app.data_ingestion import load_pdf

@patch("app.data_ingestion.open")
def test_load(m):
    pdf_path = "/tmp/test_valid.pdf"
    load_pdf(pdf_path)
"""
GUARD_GOOD = """
from app.data_ingestion import load_pdf

def test_load(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert load_pdf(str(p))
"""
GUARD_EXPECTED = """
import pytest
from app.data_ingestion import load_pdf

def test_load_missing():
    with pytest.raises(FileNotFoundError):
        load_pdf("/nonexistent/x.pdf")
"""


def test_a_literal_path_that_the_function_checks_first_is_rejected(ctx):
    with pytest.raises(ValueError, match=r"checks that `pdf_path` exists.*tmp_path"):
        gate(ctx, GUARD_BAD)
    with pytest.raises(ValueError, match="never makes it exist"):
        gate(ctx, GUARD_VARIABLE)
    gate(ctx, GUARD_GOOD)
    gate(ctx, GUARD_EXPECTED)  # a test that expects the error is fine


def test_sources_that_do_not_parse_or_are_missing_never_break_the_gate(tmp_path):
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    static_gate(
        RepoContext(root=tmp_path, source_files=["broken.py", "gone.py"]),
        "tests/t.py",
        "def test_a():\n    assert 1\n",
    )


# --- the other guardrail pieces ---------------------------------------------------------------------------------------


def test_the_wrapper_import_message_names_the_missing_package():
    text = "ImportError: Could not import chromadb python package. Please install it with `pip install chromadb`."
    assert env_check.missing_module_names(text) == ["chromadb"]
    assert env_check.missing_module_names("No module named 'a.b' and Could not import c python package") == [
        "a",
        "c",
    ]
    found = classify(text, "python")
    assert found[0].code == "EXEC-101" and found[0].detail["module"] == "chromadb"
    from maf_test_harness.errors import Context

    assert classify(text, "python", Context(missing_packages=["chromadb"]))[0].code == "ENV-PY-007"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("AttributeError: '_AsyncGeneratorContextManager' object has no attribute '__anext__'", "async with"),
        (
            "unittest.mock.InvalidSpecError: Cannot autospec attr 'chain' ... has already been mocked out",
            "MagicMock",
        ),
        ("ValidationError: instance of Runnable expected (type=type_error.arbitrary_type)", "FakeListLLM"),
        ("AssertionError: Missing 'Table of Contents' radio option", "AppTest"),
    ],
)
def test_the_heal_loop_gets_a_concrete_remedy_for_each_pitfall(message, expected):
    assert expected in triage.remedy_for(message)


def test_qa_names_declared_packages_that_never_installed_as_environment_actions():
    updates = triage.build_updates(
        [], [], [], [], {}, {"missing_packages": ["chromadb"], "missing_prerequisites": []}
    )
    assert "chromadb" in updates["environment_actions"][0]["action"]
    assert "guardrail" in updates["environment_actions"][0]["action"]


def test_live_ui_levels_follow_the_browser_tooling_of_a_python_environment():
    def levels(tooling, primary="python", url="http://127.0.0.1:8501"):
        state = {"app_url": url, "stack": {"primary": primary}, "environment": {"ui_tooling": tooling}}
        return wf._allowed_levels(state) & wf.LIVE_APP_LEVELS

    assert levels({"playwright": True, "streamlit": True}) == {"ui", "e2e", "accessibility"}
    assert levels({"playwright": False, "streamlit": True}) == {"ui"}  # AppTest still works
    assert levels({"playwright": False, "streamlit": False}) == set()
    assert levels(None) == {"ui", "e2e", "accessibility"}  # environment not inspected: unchanged behaviour
    assert levels({"playwright": False, "streamlit": False}, primary="node") == {"ui", "e2e", "accessibility"}
    assert levels({"playwright": False, "streamlit": True}, url=None) == set()


def test_the_generation_policy_states_the_environment_facts_to_the_model():
    state = {
        "environment": {
            "python_version": "3.11",
            "missing_packages": ["chromadb"],
            "ui_tooling": {"playwright": False, "streamlit": True},
        }
    }
    env = wf._generation_policy({"languages": {"Python": 1}}, state)["environment"]
    assert env["python"] == "3.11" and env["unavailable_packages"] == ["chromadb"]
    assert env["ui_tooling"] == {"playwright": False, "streamlit": True}
    assert "stub it in sys.modules" in env["rule"] and json.dumps(env)
    assert wf._generation_policy({"languages": {}}, {})["environment"]["unavailable_packages"] == []


def test_ui_tooling_reads_the_installed_distributions(tmp_path, monkeypatch):
    monkeypatch.setattr(env_check, "installed_distributions", lambda ws: {"streamlit", "pytest"})
    assert env_check.ui_tooling(tmp_path) == {"playwright": False, "streamlit": True}
    monkeypatch.setattr(env_check, "installed_distributions", lambda ws: None)
    assert env_check.ui_tooling(tmp_path) is None
