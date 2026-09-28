"""numpy 로 만든 아주 작은 torch 대역. TabularML 의 제어 흐름(콜백·취소·조기 종료·발산·재현성)을 torch 없이도 검증한다.

실제 torch 와 같은 이름·호출 규칙만 흉내 내며(Linear/ReLU/Dropout/Sequential, CE/MSE, Adam, 수동 역전파),
torch API 자체의 동작을 보증하지는 않는다. 그 부분은 실제 torch 가 있는 환경(컨테이너)에서 같은 테스트가 다시 돈다.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np


class T:
    """텐서 대역: numpy 배열 + (있다면) 역전파 함수."""

    def __init__(self, data, backprop=None):
        self.data = np.asarray(data)
        self.backprop = backprop

    shape = property(lambda self: self.data.shape)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return T(self.data[idx.data if isinstance(idx, T) else idx])

    def __float__(self):
        return float(self.data)

    def __mul__(self, k):
        return T(self.data * k)

    def __iadd__(self, other):
        self.data = self.data + (other.data if isinstance(other, T) else other)
        return self

    def to(self, device):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.data

    def detach(self):
        return T(self.data)

    def clone(self):
        return T(self.data.copy())

    def argmax(self, dim):
        return T(self.data.argmax(axis=dim))

    def squeeze(self, dim):
        parent = self
        return T(self.data.squeeze(dim), (lambda g: parent.backprop(g.reshape(parent.data.shape))) if self.backprop else None)

    def backward(self):
        self.backprop()


class Param:
    def __init__(self, data):
        self.data = data
        self.grad = np.zeros_like(data)


class Linear:
    def __init__(self, n_in, n_out, rng):
        bound = 1 / np.sqrt(n_in)
        self.W = Param(rng.uniform(-bound, bound, (n_out, n_in)).astype(np.float32))
        self.b = Param(rng.uniform(-bound, bound, n_out).astype(np.float32))

    def params(self):
        return {"W": self.W, "b": self.b}

    def forward(self, x, training, rng):
        self.x = x
        return x @ self.W.data.T + self.b.data

    def backward(self, g):
        self.W.grad += g.T @ self.x
        self.b.grad += g.sum(axis=0)
        return g @ self.W.data


class ReLU:
    def params(self):
        return {}

    def forward(self, x, training, rng):
        self.mask = x > 0
        return x * self.mask

    def backward(self, g):
        return g * self.mask


class Dropout:
    def __init__(self, p):
        self.p = p

    def params(self):
        return {}

    def forward(self, x, training, rng):
        if not training or self.p == 0:
            self.mask = np.ones_like(x)
        else:
            self.mask = (rng.random(x.shape) >= self.p).astype(np.float32) / (1 - self.p)
        return x * self.mask

    def backward(self, g):
        return g * self.mask


class Sequential:
    def __init__(self, *layers):
        self.layers = list(layers)
        self.training = True
        self.rng = None  # FakeTorch 가 채운다

    def to(self, device):
        return self

    def train(self):
        self.training = True

    def eval(self):
        self.training = False

    def parameters(self):
        return [p for l in self.layers for p in l.params().values()]

    def state_dict(self):
        return {f"{i}.{k}": T(p.data) for i, l in enumerate(self.layers) for k, p in l.params().items()}

    def load_state_dict(self, state):
        for i, l in enumerate(self.layers):
            for k, p in l.params().items():
                p.data = state[f"{i}.{k}"].data.copy()

    def __call__(self, x):
        h = x.data
        for l in self.layers:
            h = l.forward(h, self.training, self.rng)

        def back(g):
            for l in reversed(self.layers):
                g = l.backward(g)

        return T(h, back)


class CrossEntropyLoss:
    def __call__(self, out, y):
        z = out.data - out.data.max(axis=1, keepdims=True)
        p = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
        n = len(y)
        rows = np.arange(n)
        loss = -np.log(p[rows, y.data] + 1e-12).mean()

        def back():
            g = p.copy()
            g[rows, y.data] -= 1
            out.backprop(g / n)

        return T(loss, back)


class MSELoss:
    def __call__(self, out, y):
        diff = out.data - y.data
        loss = (diff ** 2).mean()
        return T(loss, lambda: out.backprop(2 * diff / len(diff)))


class Adam:
    def __init__(self, params, lr):
        self.params, self.lr, self.t = list(params), lr, 0
        self.m = [np.zeros_like(p.data) for p in self.params]
        self.v = [np.zeros_like(p.data) for p in self.params]

    def zero_grad(self):
        for p in self.params:
            p.grad = np.zeros_like(p.data)

    def step(self):
        self.t += 1
        for i, p in enumerate(self.params):
            self.m[i] = 0.9 * self.m[i] + 0.1 * p.grad
            self.v[i] = 0.999 * self.v[i] + 0.001 * p.grad ** 2
            mh = self.m[i] / (1 - 0.9 ** self.t)
            vh = self.v[i] / (1 - 0.999 ** self.t)
            p.data = (p.data - self.lr * mh / (np.sqrt(vh) + 1e-8)).astype(np.float32)


class Generator:
    def __init__(self):
        self.rng = np.random.default_rng(0)

    def manual_seed(self, seed):
        self.rng = np.random.default_rng(seed)
        return self


class FakeTorch:
    """torch 모듈 대역. cuda=True 로 GPU 가 있는 것처럼 꾸밀 수 있다."""

    def __init__(self, cuda: bool = False) -> None:
        self.threads_calls: list[int] = []
        self.cuda = SimpleNamespace(is_available=lambda: cuda, get_device_name=lambda i: "Fake GPU")
        self.rng = np.random.default_rng(0)
        self.nn = SimpleNamespace(
            Linear=lambda a, b: Linear(a, b, self.rng), ReLU=ReLU, Dropout=Dropout,
            Sequential=self._sequential, CrossEntropyLoss=CrossEntropyLoss, MSELoss=MSELoss,
        )
        self.optim = SimpleNamespace(Adam=lambda params, lr: Adam(params, lr))
        self.Generator = Generator

    def _sequential(self, *layers):
        seq = Sequential(*layers)
        seq.rng = self.rng
        return seq

    def set_num_threads(self, n):
        self.threads_calls.append(n)

    def manual_seed(self, seed):
        self.rng = np.random.default_rng(seed)

    def device(self, name):
        return name

    def from_numpy(self, arr):
        return T(arr)

    def zeros(self, shape, device=None):
        return T(np.zeros(shape, dtype=np.float64))

    def randperm(self, n, generator=None):
        return T(generator.rng.permutation(n))

    def cat(self, tensors, dim=0):
        return T(np.concatenate([t.data for t in tensors], axis=dim))

    def softmax(self, x, dim):
        z = x.data - x.data.max(axis=dim, keepdims=True)
        e = np.exp(z)
        return T(e / e.sum(axis=dim, keepdims=True))

    @contextmanager
    def no_grad(self):
        yield
