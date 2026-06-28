/**
 * CharacterSeed Web 入口
 * 注意：import './composables/useTheme' 必须在最顶部，
 * 触发 useTheme 的模块级 init()，把 <html data-theme="..."> 提前设好，避免 FOUC。
 */
import './composables/useTheme'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'

const app = createApp(App)
app.use(router)
app.mount('#app')
