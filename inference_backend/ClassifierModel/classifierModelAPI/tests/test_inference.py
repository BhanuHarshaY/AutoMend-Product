"""Unit tests for the tokenization + forward-pass pipeline."""

from __future__ import annotations

from app.inference import logs_to_text, run_inference


class TestLogsToText:
    def test_concatenates_bodies_with_newlines(self):
        logs = [{"body": "line one"}, {"body": "line two"}]
        assert logs_to_text(logs, max_logs=10) == "line one\nline two"

    def test_respects_max_logs(self):
        logs = [{"body": f"line {i}"} for i in range(10)]
        result = logs_to_text(logs, max_logs=3)
        assert result == "line 0\nline 1\nline 2"

    def test_skips_blank_bodies(self):
        logs = [{"body": "real"}, {"body": ""}, {"body": "   "}, {"attributes": {}}]
        assert logs_to_text(logs, max_logs=10) == "real"

    def test_all_blank_returns_empty(self):
        logs = [{"body": ""}, {"attributes": {}}, {"body": "   "}]
        assert logs_to_text(logs, max_logs=10) == ""

    def test_coerces_non_string_bodies(self):
        logs = [{"body": 42}, {"body": {"nested": "dict"}}]
        text = logs_to_text(logs, max_logs=10)
        assert "42" in text
        assert "nested" in text

    def test_handles_empty_list(self):
        assert logs_to_text([], max_logs=10) == ""


class TestRunInference:
    def test_happy_path_returns_argmax(self, mock_model_factory, mock_tokenizer, cpu_device, oom_logs):
        # Logits favor class 1 (Resource_Exhaustion)
        model = mock_model_factory([0.1, 5.0, 0.1, 0.1, 0.1, 0.1, 0.1])

        class_id, confidence = run_inference(model, mock_tokenizer, oom_logs, 200, cpu_device)

        assert class_id == 1
        assert 0.0 < confidence <= 1.0
        assert confidence > 0.9   # softmax(5, 0.1…) is heavily peaked

    def test_normal_class_when_all_bodies_blank(self, mock_model_factory, mock_tokenizer, cpu_device, empty_body_logs):
        # Mock model is wired to return non-zero logits, but we expect the
        # short-circuit to skip the forward pass entirely.
        model = mock_model_factory([0.0, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        class_id, confidence = run_inference(model, mock_tokenizer, empty_body_logs, 200, cpu_device)

        assert class_id == 0            # Normal
        assert confidence == 1.0
        model.assert_not_called()       # Forward pass skipped

    def test_tokenizer_receives_concatenated_bodies(self, mock_model_factory, mock_tokenizer, cpu_device):
        model = mock_model_factory([5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        logs = [{"body": "alpha"}, {"body": "beta"}]

        run_inference(model, mock_tokenizer, logs, 200, cpu_device)

        call_args = mock_tokenizer.call_args
        assert call_args is not None
        text_arg = call_args[0][0]
        assert "alpha" in text_arg
        assert "beta" in text_arg

    def test_tokenizer_called_with_truncation(self, mock_model_factory, mock_tokenizer, cpu_device, oom_logs):
        model = mock_model_factory([5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        run_inference(model, mock_tokenizer, oom_logs, 200, cpu_device)

        kwargs = mock_tokenizer.call_args.kwargs
        assert kwargs.get("truncation") is True
        assert kwargs.get("max_length") == 512
        assert kwargs.get("padding") == "max_length"

    def test_confidence_is_probability(self, mock_model_factory, mock_tokenizer, cpu_device, gpu_logs):
        # All logits equal → uniform softmax ≈ 1/7
        model = mock_model_factory([1.0] * 7)

        class_id, confidence = run_inference(model, mock_tokenizer, gpu_logs, 200, cpu_device)

        assert 0 <= class_id <= 6
        assert abs(confidence - (1.0 / 7.0)) < 1e-5
