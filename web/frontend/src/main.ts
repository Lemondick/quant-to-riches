import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { virtualScroll } from './directives/virtualScroll'
import './styles/theme.css'
import './styles/responsive.css'

createApp(App).directive('virtual-scroll', virtualScroll).use(router).mount('#app')
