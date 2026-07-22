import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { trap } from './directives/trap'
import './style.css'

createApp(App).use(createPinia()).use(router).directive('trap', trap).mount('#app')
