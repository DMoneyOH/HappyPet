---
layout: default
title: Dog Product Reviews
description: In-depth reviews of the best dog products - collars, harnesses, toys, beds and more. Honest picks for every budget.
permalink: /dogs/
---
{%- assign dog_only = site.posts | where: "species", "dog" -%}
{%- assign dog_both = site.posts | where: "species", "both" -%}
{%- assign dog_posts = dog_only | concat: dog_both -%}
<section class="section">
  <div class="wrap">
    <div class="section-head">
      <h1>for dogs</h1>
      <span class="count">{{ dog_posts | size }} reviews</span>
    </div>
    <div class="entries">
      {% if dog_posts.size == 0 %}
      <div class="empty">
        <b>Dog reviews are on the way</b>
        <span>We are writing in-depth guides right now. Check back shortly.</span>
      </div>
      {% else %}
        {% include post-list.html posts=dog_posts %}
      {% endif %}
    </div>
  </div>
</section>
