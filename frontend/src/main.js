import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { trap } from './directives/trap'
import { installErrorLog } from './utils/errorLog'
import './style.css'

// Capture client-side errors early so a later bug report can include them.
installErrorLog()

createApp(App).use(createPinia()).use(router).directive('trap', trap).mount('#app')
