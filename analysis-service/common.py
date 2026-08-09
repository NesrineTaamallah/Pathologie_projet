
import base64
import io
import matplotlib
matplotlib.use("Agg")  
import matplotlib.pyplot as plt


def figure_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


class Notes:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, texte: str):
        self.lines.append(texte)

    def __call__(self, texte: str):
        self.add(texte)
