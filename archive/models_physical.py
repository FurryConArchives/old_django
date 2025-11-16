from django.db import models

class PhysicalInventoryItem(models.Model):
    con = models.CharField(max_length=200, help_text="Convention or event name")
    year = models.IntegerField(null=True, blank=True, help_text="Year of the convention")
    item_type = models.CharField(max_length=100, blank=True, help_text="Type of item (e.g. Conbook, Poster, T-Shirt)")
    quantity = models.PositiveIntegerField(default=1, help_text="Number of physical copies")
    donators = models.CharField(max_length=255, blank=True, help_text="Donator(s) name(s)")
    notes = models.TextField(blank=True, help_text="Additional notes")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "The Vault Item"
        verbose_name_plural = "The Vault Items"
        ordering = ["-added_at", "con"]

    def __str__(self):
        year_str = f" {self.year}" if self.year else ""
        return f"{self.con}{year_str} - {self.item_type} ({self.quantity}) - {self.donators}"


class VaultContributorProfile(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ('telegram', 'Telegram'),
        ('discord', 'Discord'),
    ]

    account_type = models.CharField(max_length=16, choices=ACCOUNT_TYPE_CHOICES)
    account_id = models.CharField(
        max_length=100,
        help_text='Telegram username (without @) or Discord user ID',
    )
    avatar = models.ImageField(
        upload_to='vault_contributor_avatars/',
        blank=True,
        null=True,
        help_text='Optional custom avatar override for this contributor',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vault Contributor Profile"
        verbose_name_plural = "Vault Contributor Profiles"
        constraints = [
            models.UniqueConstraint(
                fields=['account_type', 'account_id'],
                name='unique_vault_contributor_account',
            ),
        ]
        ordering = ['account_type', 'account_id']

    def __str__(self):
        return f"{self.get_account_type_display()}: {self.account_id}"