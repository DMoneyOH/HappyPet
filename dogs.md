---
layout: default
title: Dog Product Reviews | Happy Pet Product Reviews
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
        {% for post in dog_posts %}
          {% if forloop.index == 1 %}
            {% include post-card.html post=post rank=forloop.index variant="major" eager=true %}
          {% elsif forloop.index <= 3 %}
            {% include post-card.html post=post rank=forloop.index variant="wide" eager=true %}
          {% elsif forloop.index <= 7 %}
            {% include post-card.html post=post rank=forloop.index eager=true %}
          {% else %}
            {% include post-card.html post=post rank=forloop.index %}
          {% endif %}
        {% endfor %}
      {% endif %}
    </div>
  </div>
</section>
