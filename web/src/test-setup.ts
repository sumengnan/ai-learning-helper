// Vitest (jsdom) 全局垫片：jsdom 不实现 ResizeObserver，而 EllipsisText 等组件在挂载时会用到。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// @ts-expect-error jsdom 环境无此全局，测试期打桩即可
globalThis.ResizeObserver = globalThis.ResizeObserver || ResizeObserverStub;
