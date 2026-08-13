import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

// Routes beyond the dashboard land in later tasks (auth, prompts, runs, …);
// this scaffold wires just enough for the app shell to render and navigate.
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
  ],
})

export default router
