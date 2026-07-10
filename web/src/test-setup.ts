// Vitest (jsdom) 全局垫片：jsdom 不实现 ResizeObserver，而 EllipsisText 等组件在挂载时会用到。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom 环境无此全局，测试期打桩即可（stub 不完整实现接口，显式 cast 以在各 TS 版本下都过）
globalThis.ResizeObserver = globalThis.ResizeObserver
  || (ResizeObserverStub as unknown as typeof ResizeObserver);
